from collections import Counter, defaultdict
from statistics import median
from scipy.stats import norm


COLUMNS = (
    ("sequence", "先唱条件"), ("part", "声部"), ("count", "配对人数"), ("tied", "未变化"),
    ("better", "缩小偏差"), ("worse", "增大偏差"), ("effect", "中位改善（拍）"),
    ("rank", "正秩和"), ("p", "整体 p 值"), ("method", "检验方法"),
)


def validate(row):
    if not all(0 <= row[key] <= 10 for key in ("usual","new")):
        raise ValueError("偏差以绝对拍数计且在 0–10 之间")


def changes(group):
    return [round(1000*row["usual"]) - round(1000*row["new"]) for row in group]


def measures(group):
    deltas = changes(group)
    signs = Counter((value>0)-(value<0) for value in deltas)
    return dict(count=len(group),tied=signs[0],better=signs[1],worse=signs[-1],effect=median(deltas)/1000)


def signed_rank(deltas):
    items = sorted((abs(value),value>0) for value in deltas if value)
    if not items:
        return 0, 1.0, "无非零配对"
    weights = {magnitude:count for magnitude,count in Counter(magnitude for magnitude,_ in items).items()}
    ranks = {}
    offset = 0
    for magnitude, weight in weights.items():
        ranks[magnitude] = 2*offset+weight+1
        offset += weight
    scores = [ranks[magnitude] for magnitude, _ in items]
    positive = sum(ranks[magnitude] for magnitude, sign in items if sign)
    total = sum(scores)
    edge = min(positive,total-positive)
    if len(items) > 160:
        # Representing doubled midranks avoids losing tied fractional ranks.
        variance = sum(score**2 for score in scores)/4
        p = min(1.0,2*float(norm.cdf((edge+0.5-total/2)/variance**0.5)))
        return positive/2,p,"大量非零配对，正态近似"
    distribution = [1] + [0]*edge
    for score in scores:
        for value in range(edge,score-1,-1):
            distribution[value] += distribution[value-score]
    p = min(1.0,2*sum(distribution)/(2**len(items)))
    return positive/2,p,"精确符号翻转"


def analyze(rows, parameters):
    if not rows:
        raise ValueError("没有已完成双条件的试唱记录")
    alpha = parameters.get("alpha",0.05)
    seen = set()
    sections, orders = defaultdict(list),defaultdict(list)
    for row in rows:
        validate(row)
        if row["singer"] in seen:
            raise ValueError(f"演唱者 {row['singer']} 存在重复试唱配对")
        seen.add(row["singer"])
        sections[row["part"]].append(row)
        orders[row["sequence"]].append(row)
    rank,p,method = signed_rank(changes(rows))
    overall = measures(rows)
    if p <= alpha and overall["effect"] != 0:
        title = "新翻页提示缩小节拍偏差" if overall["effect"] > 0 else "新翻页提示增大节拍偏差"
    else:
        title = "未达到有明确方向的预设偏差改善线"
    result = [dict(sequence="总计",rank=rank,p=round(p,9),method=method,**overall)]
    result.extend(dict(sequence=sequence,method="次序对照",**measures(group)) for sequence,group in sorted(orders.items()))
    result.extend(dict(sequence="所有条件",part=part,method="声部说明",**measures(group)) for part,group in sorted(sections.items()))
    return report(title,
                  f"正值表示绝对偏差缩小。±0.001 拍作为计量粒度，正秩和为 {rank:g}，采用{method}，预设 α={alpha:g}。",
                  [("双条件配对",len(rows),"人"),("中位改善",overall["effect"],"拍"),("p 值",round(p,9),"")],
                  COLUMNS,result,"分声部的中位改善",[(part,measures(group)["effect"]) for part,group in sorted(sections.items())],"拍")


def seed():
    parts = ["女高音","女低音","男高音","男低音"]
    return [dict(singer=f"XA-{index+1:03d}",part=parts[index%4],
                 sequence="新提示" if index//4%2 else "常规提示",
                 usual=round(0.35+(index*7%13)*0.11,2),
                 new=round(0.21+(index*3%11)*0.074 + (0.5 if index%13==5 else 0),3))
            for index in range(60)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
