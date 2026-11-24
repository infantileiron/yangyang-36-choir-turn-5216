export const Role = Object.freeze({ VIEWER: 'viewer', ANALYST: 'analyst', SUPERVISOR: 'supervisor' });
export const RunStatus = Object.freeze({ QUEUED: 'queued', SUCCEEDED: 'succeeded', FAILED: 'failed' });
export const Decision = Object.freeze({ APPROVED: 'approved', RETURNED: 'returned' });
export const SingerState = Object.freeze({ PAIRED: 'paired', MISSING: 'missing', BLOCKED: 'blocked' });
export const roleName = {viewer: '只读查看', analyst: '业务分析员', supervisor: '复核负责人'};
export const statusName = {queued: '等待计算', succeeded: '等待复核', failed: '计算失败', approved: '已签核', returned: '已退回'};
export const stateName = {paired: '可入样', missing: '缺读数', blocked: '冲突搁置'};
