from enum import StrEnum


class Role(StrEnum):
    VIEWER = 'viewer'
    ANALYST = 'analyst'
    SUPERVISOR = 'supervisor'


class RunStatus(StrEnum):
    QUEUED = 'queued'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


class Decision(StrEnum):
    APPROVED = 'approved'
    RETURNED = 'returned'


class Cue(StrEnum):
    # 两种提示条件，各对应一条节拍偏差读数
    USUAL = 'usual'
    NEW = 'new'


class ReadingStatus(StrEnum):
    # 当前生效读数
    ACTIVE = 'active'
    # 被更高版本取代的旧读数
    SUPERSEDED = 'superseded'
    # 同一提示同一版本读数冲突，搁置
    CONFLICT = 'conflict'


class SingerState(StrEnum):
    # 两条提示读数齐备且无冲突，可入样
    PAIRED = 'paired'
    # 尚缺某种提示的生效读数
    MISSING = 'missing'
    # 存在未被更高版本解开的冲突，整人搁置
    BLOCKED = 'blocked'
