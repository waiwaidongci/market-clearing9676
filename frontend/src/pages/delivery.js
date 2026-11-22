import {feedback} from '../components/common/fields.js';
import {panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {DAY_STATUS, REVIEW} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';
import {runPage} from './run.js';

export async function deliveryPage(route, refresh) {
  const {spec, runs} = workspace;
  const message = feedback();
  const days = spec.scheduled ? await workspace.settlementDays() : [];
  const artifacts = table([
    {label:'报告', render:row=>link(row.name,`#results/${row.id}`)},
    {label:'复核', render:row=>pill(REVIEW[row.review],row.review)},
    {label:'计算时间', render:row=>date(row.finished_at)},
    {label:'下载', render:row=>{
      const download = button('下载 CSV',{class:'quiet'});
      download.addEventListener('click', handler(download,message,() =>
        workspace.download(`/runs/${row.id}/export`,`${spec.short_name}-${row.id}.csv`)));
      return download;
    }},
  ], runs.filter(row=>row.status==='complete'), {caption:'可导出的结果', empty:'完成计算后会生成对应的报告。'});
  const daily = spec.scheduled ? panel('按结算日交付', p('每日按结算日择优批次，未通过的日期挂起等待补账，不影响后续日期。','subtle'), table([
    {key:'report_day',label:'结算日'},
    {label:'状态',render:row=>pill(DAY_STATUS[row.status] || row.status, row.status)},
    {key:'step',label:'卡在哪一步'},
    {label:'实际批次',render:row=>row.dataset_id
      ? link(row.dataset_name || `批次 ${row.dataset_id}`, `#inputs/${row.dataset_id}`) : '—'},
    {key:'detail',label:'清分结论'},
    {label:'收件团队',render:row=>row.recipient || '—'},
    {label:'交付时间',render:row=>date(row.delivered_at)},
    {label:'操作',render:row=>{
      const actions = el('span',{class:'actions'});
      if (row.run_id) actions.append(link('查看报告',`#results/${row.run_id}`,{class:'quiet'}));
      if (session.canReview && row.status === 'blocked') {
        const retry = button('重新择优',{class:'quiet'});
        retry.addEventListener('click',handler(retry,message,async()=>{await workspace.retryDay(row.id);await refresh();}));
        actions.append(retry);
      }
      return actions.childElementCount ? actions : '—';
    }},
  ], days, {caption:'结算日状态', empty:'定时计划到期后会在此显示每日进度。'})) : null;
  const delivery = spec.scheduled ? panel('站内分发回执', table([
    {key:'recipient',label:'收件团队'},
    {label:'报告日',render:row=>row.report_day || '—'},
    {label:'回执',render:row=>pill(row.state==='delivered'?'已投递':'已拦截',row.state)},
    {key:'detail',label:'说明'}, {label:'时间',render:row=>date(row.created_at)},
    {label:'查看',render:row=>link(`运行 ${row.run_id}`,`#results/${row.run_id}`)},
    {label:'下载',render:row=>{
      if (row.state !== 'delivered') return '—';
      const download = button('下载 CSV',{class:'quiet'});
      download.addEventListener('click', handler(download,message,() =>
        workspace.download(`/deliveries/${row.id}/export`,`${spec.short_name}-${row.report_day || row.run_id}.csv`)));
      return download;
    }},
  ],await workspace.deliveries(),{caption:'站内收件箱',empty:'计划到时执行，回执将在此更新。'})) : null;
  return el('div',{}, panel(spec.pages[5], p('按运行批次下载 CSV，复核状态随报告保留。','subtle'), artifacts, message),
    daily, delivery, spec.scheduled && await runPage({...route,scheduling:true},refresh));
}
