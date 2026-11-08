// OpenRouter 独立只读 Worker：仅允许明确的 :free 模型，绝不自动切换付费模型。
import { readFile, realpath, writeFile } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { AgentWorker, BridgeClient } from '../../node-worker/src/index.js';

export const AGENT_ID = 'openrouter_1';
export const MODEL = 'openrouter/free';
const ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions';
export function allowedModel(model) {
  // openrouter/free 会自动选择免费模型；其他模型必须明确标记 :free。
  if (model !== MODEL && !/^[a-z0-9][a-z0-9._-]*\/[a-z0-9][a-z0-9._-]*:free$/i.test(model)) {
    throw new Error('openrouter_nonfree_model_denied');
  }
  return model;
}
export async function queryFree(prompt, {key, model=MODEL, fetchImpl=fetch, signal}={}) {
  allowedModel(model);
  if (!key || !key.trim()) throw new Error('openrouter_key_missing');
  if (typeof prompt !== 'string' || prompt.length > 10000) throw new Error('openrouter_prompt_invalid');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  const abort = () => controller.abort();
  signal?.addEventListener('abort',abort,{once:true});
  try {
    const response = await fetchImpl(ENDPOINT,{method:'POST',signal:controller.signal,
      headers:{Authorization:`Bearer ${key}`, 'Content-Type':'application/json'},
      body:JSON.stringify({model, messages:[{role:'system',content:'You are a read-only research reviewer. Reply in Chinese. Never claim to have executed code.'},{role:'user',content:prompt}], max_tokens:900,stream:false})});
    if (!response.ok) throw new Error(`openrouter_http_${response.status}`);
    const result=await response.json();
    const content=result?.choices?.[0]?.message?.content;
    if (typeof content!=='string' || !content.trim()) throw new Error('openrouter_empty_result');
    return content;
  } finally {clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
}
export async function handleTask(ctx,{query=queryFree,key}={}) {
  const task=ctx.task;
  const name=task?.metadata?.expected_artifacts?.[0] || 'review.md';
  if (typeof name!=='string' || !/^[a-zA-Z0-9_.-]+\.md$/.test(name) || name.startsWith('.')) throw new Error('openrouter_artifact_denied');
  const root=await realpath(resolve(task.workspace));
  const output=resolve(root,name);
  if(!output.startsWith(root+sep)) throw new Error('openrouter_workspace_escape');
  const text=await query(String(task.title||'')+'\n'+String(task.prompt||''),{key,signal:ctx.signal});
  await writeFile(output,text+'\n',{flag:'wx',mode:0o600});
  return {artifacts:[{path:name,kind:'file',label:name}]};
}
export async function main() {
  // 缺少独立凭据或明确开关时不注册，不占在线名额。
  if(process.env.AI_TEAM_OPENROUTER_FREE_APPROVED!=='1') throw new Error('openrouter_free_not_approved');
  const key=(await readFile(process.env.OPENROUTER_API_KEY_FILE,'utf8')).trim();
  const bootstrap=(await readFile(process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE,'utf8')).trim();
  if(!bootstrap) throw new Error('openrouter_bootstrap_missing');
  const bridgeUrl=process.env.AI_TEAM_BRIDGE_URL || 'http://127.0.0.1:8765';
  const client=new BridgeClient({baseUrl:bridgeUrl});
  // 不在启动时消耗免费额度：只验证配置和凭据文件，真实任务才调用 API。
  await client.reportReadiness({agentId:AGENT_ID,modelAvailable:true,bootstrapToken:bootstrap});
  await new AgentWorker({bridgeUrl,agentId:AGENT_ID,label:'OpenRouter Free (Read Only)',capabilities:['research','code_review','planning'],onTask:(ctx)=>handleTask(ctx,{key})}).start();
}
if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(e=>{console.error('OpenRouter worker startup:',e.message);process.exitCode=1;});
}
