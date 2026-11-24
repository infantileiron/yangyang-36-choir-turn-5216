import { download, get, importFile, post } from '../api/client.js';
import { stateBadge, table } from '../components/common/data.js';
import { intro } from '../components/common/frame.js';
import { fieldControl, formError } from '../components/common/forms.js';
import { Role } from '../constants/enums.js';
import { bindDownloads, bindForm, notify } from '../hooks/actions.js';
import { formValues } from '../types/form.js';
import { esc, displayValue, timeLabel } from '../utils/html.js';

const FILTERS = [
  {key: 'all', label: '全部'},
  {key: 'paired', label: '可入样'},
  {key: 'missing', label: '缺读数'},
  {key: 'blocked', label: '冲突搁置'},
];

function cueCell(value, version) {
  if (value === null || value === undefined) return '<span class="cell-missing">缺</span>';
  return `${esc(displayValue(value))}<small>v${esc(version)}</small>`;
}

function issueCell(row) {
  if (row.state === 'blocked') return `<span class="issue issue-blocked">冲突：${esc(row.conflict_cues.join('、'))}</span>`;
  if (row.state === 'missing') return `<span class="issue issue-missing">缺：${esc(row.missing_cues.join('、'))}</span>`;
  return '<span class="issue issue-ok">双条件齐备</span>';
}

function summaryCards(summary) {
  const cards = [
    {label: '演唱者', value: summary.singers, tone: ''},
    {label: '可入样配对', value: summary.paired, tone: 'paired'},
    {label: '缺读数', value: summary.missing, tone: 'missing'},
    {label: '冲突搁置', value: summary.blocked, tone: 'blocked'},
  ];
  return `<div class="summary-cards">${cards.map(card =>
    `<div class="summary-card ${card.tone}"><b>${esc(card.value)}</b><span>${esc(card.label)}</span></div>`).join('')}</div>`;
}

export async function observationPage(spec, user, route) {
  const state = FILTERS.some(item => item.key === route.id) ? route.id : 'all';
  const writable = user.role !== Role.VIEWER;
  const [summary, ledger, readings, batches] = await Promise.all([
    get('/ledger/summary'),
    get(`/ledger?state=${state}`),
    get('/readings?limit=15'),
    get('/batches'),
  ]);
  const counts = {all: summary.singers, paired: summary.paired, missing: summary.missing, blocked: summary.blocked};
  return `${intro('试唱数据台账', '按演唱者与提示条件分批导入读数；两种提示读数齐备才登记配对，冲突者待更高版本解开。', `<span class="count">${summary.paired} <small>个可入样配对</small></span>`)}
    ${summaryCards(summary)}
    ${writable ? `<div class="tools"><details><summary>登记一条读数</summary><form id="record-form"><div class="form-grid">${spec.fields.map(field => fieldControl(field)).join('')}</div>${formError}<button class="primary">保存读数</button></form></details>
    <details><summary>按演唱者/提示条件分批导入 CSV</summary><form id="import-form"><p>使用 UTF-8 CSV，每批最多 500 条读数。同一份文件重复提交不会新增读数；迟到的旧版本只归档，不覆盖新版本。</p><button type="button" data-download="/readings/template">下载模板</button><label class="field"><span>CSV 文件</span><input type="file" name="file" accept=".csv,text/csv" required></label>${formError}<button class="primary">导入本批</button></form></details></div>` : ''}
    <section class="panel"><div class="panel-heading"><h2>${esc(spec.pair_name)}台账</h2>
      <div class="filter-tabs" role="tablist">${FILTERS.map(item =>
        `<a class="filter-tab ${state === item.key ? 'active' : ''}" href="#observations/${item.key}">${esc(item.label)} <b>${esc(counts[item.key])}</b></a>`).join('')}</div>
    </div>
    ${table([
      {key: 'singer', label: '演唱者匿名号'},
      {key: 'part', label: '声部'},
      {label: '常规提示读数', html: row => cueCell(row.usual, row.usual_version)},
      {label: '新提示读数', html: row => cueCell(row.new, row.new_version)},
      {label: '入样状态', html: stateBadge},
      {label: '待处理', html: issueCell},
      {label: '读数历史', html: row => `<button class="link-button" data-singer="${esc(row.singer)}">查看版本</button>`},
    ], ledger, '当前没有试唱读数。分析员可先导入 CSV 或登记一条读数。')}
    <div id="singer-history" class="singer-history" hidden></div></section>
    <div class="split">
      <section class="panel"><div class="panel-heading"><h2>最近读数（共 ${esc(readings.total)} 条）</h2></div>
      ${table([
        {key: 'singer', label: '演唱者'},
        {label: '提示条件', html: row => esc(row.cue_label)},
        {key: 'version', label: '版本', html: row => `v${esc(row.version)}`},
        {key: 'deviation', label: '偏差（拍）'},
        {key: 'status', label: '读数状态', html: row => `<span class="reading-status reading-status-${esc(row.status)}">${esc(readingStatusName(row.status))}</span>`},
        {label: '时间', html: row => timeLabel(row.created_at)},
      ], readings.items, '暂无读数。')}</section>
      <section class="panel"><div class="panel-heading"><h2>导入批次</h2></div>
      ${table([
        {key: 'id', label: '批次', html: row => `#${esc(row.id)}`},
        {key: 'label', label: '说明'},
        {key: 'actor', label: '导入人'},
        {label: '时间', html: row => timeLabel(row.received_at)},
      ], batches, '暂无批次。')}</section>
    </div>`;
}

function readingStatusName(status) {
  return {active: '生效', superseded: '已被取代', conflict: '冲突搁置'}[status] || status;
}

function historyTable(items) {
  if (!items.length) return '<div class="empty">该演唱者暂无读数。</div>';
  return table([
    {label: '提示条件', html: row => esc(row.cue_label)},
    {label: '版本', html: row => `v${esc(row.version)}`},
    {key: 'deviation', label: '偏差（拍）'},
    {key: 'sequence', label: '先唱条件'},
    {key: 'status', label: '状态', html: row => `<span class="reading-status reading-status-${esc(row.status)}">${esc(readingStatusName(row.status))}</span>`},
    {key: 'batch_id', label: '来源批次', html: row => row.batch_id ? `#${esc(row.batch_id)}` : '—'},
  ], items);
}

export function wireObservations(root, spec, refresh) {
  bindDownloads(root, download);
  bindForm(root.querySelector('#record-form'), async form => {
    const report = await post('/readings', formValues(form, spec.fields));
    notify(importNotice(report.report ?? report));
    refresh();
  });
  bindForm(root.querySelector('#import-form'), async form => {
    const report = await importFile(form.elements.file.files[0]);
    notify(importNotice(report));
    refresh();
  });
  root.querySelectorAll('[data-singer]').forEach(button => button.addEventListener('click', async () => {
    const panel = root.querySelector('#singer-history');
    panel.hidden = false;
    panel.innerHTML = `<div class="panel-heading"><h2>${esc(button.dataset.singer)} 的读数版本</h2><button type="button" id="close-history" class="link-button">收起</button></div><div class="empty">加载中…</div>`;
    const data = await get(`/readings?singer=${encodeURIComponent(button.dataset.singer)}&limit=100`);
    panel.innerHTML = `<div class="panel-heading"><h2>${esc(button.dataset.singer)} 的读数版本</h2><button type="button" id="close-history" class="link-button">收起</button></div>${historyTable(data.items)}`;
    panel.querySelector('#close-history').addEventListener('click', () => { panel.hidden = true; panel.innerHTML = ''; });
    panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }));
}

function importNotice(report) {
  if (report.replayed) return '该批次已导入过，未新增读数';
  const parts = [`新增 ${report.added} 条`];
  if (report.duplicate) parts.push(`重复跳过 ${report.duplicate}`);
  if (report.conflict) parts.push(`冲突搁置 ${report.conflict}`);
  if (report.late) parts.push(`迟到归档 ${report.late}`);
  if (report.supersede) parts.push(`新版本取代 ${report.supersede}`);
  if (report.unblocked) parts.push(`解开冲突 ${report.unblocked} 人`);
  return parts.join(' · ');
}
