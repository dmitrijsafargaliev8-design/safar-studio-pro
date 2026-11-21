import {cookies} from 'next/headers';
import {createHash,randomBytes,timingSafeEqual} from 'node:crypto';
export type Row=Record<string,any>;
export class Failure extends Error{constructor(public status:number,message:string){super(message)}}
export const fail=(status:number,msg:string):never=>{throw new Failure(status,msg)};
export function env(k:string){return process.env[k]?.trim()||fail(503,'Отсутствует серверная настройка '+k)}
export const base=()=>env('APP_URL').replace(/\/$/,'');
export const sb=()=>env('SUPABASE_URL').replace(/\/$/,'');
export const hash=(s:string)=>createHash('sha256').update(s).digest('hex');
export function secureEq(s:string,t:string){const a=Buffer.from(s),b=Buffer.from(t);return a.length===b.length&&timingSafeEqual(a,b)}
export function uuid(s:unknown):string{if(typeof s!=='string'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)))return fail(422,'Некорректный UUID');return s}
export const token=()=>randomBytes(24).toString('base64url');
export async function readResponse(r:Response){const text=await r.text();let v:Row;try{v=JSON.parse(text)}catch{v={}}if(!r.ok){const msg=String(v.message||v.detail||v.error_description||'Ошибка сервиса');throw new Failure(r.status>=500?502:r.status,msg.slice(0,260))}return v as any}
export async function db(path:string,method='GET',data?:unknown,prefer='return=representation'){
 const key=env('SUPABASE_SERVICE_ROLE_KEY');
 return readResponse(await fetch(sb()+'/rest/v1/'+path,{method,cache:'no-store',headers:{apikey:key,Authorization:'Bearer '+key,'Content-Type':'application/json',Prefer:prefer},body:data===undefined?undefined:JSON.stringify(data)}));
}
export const rpc=(name:string,data:Row)=>db('rpc/safar_'+name,'POST',data);
export async function gpu(path:string,data?:unknown){return readResponse(await fetch(env('MODAL_URL').replace(/\/$/,'')+path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+env('SAFAR_GPU_TOKEN'),'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store',signal:AbortSignal.timeout(26000)}))}
export async function ticket(owner:string,id:string,file:string){const t=await gpu('/api/tickets',{owner,job_id:id,file});return env('MODAL_URL').replace(/\/$/,'')+t.path}
export async function authCall(path:string,method='GET',data?:unknown,bearer?:string){const key=env('SUPABASE_ANON_KEY');return readResponse(await fetch(sb()+'/auth/v1/'+path,{method,cache:'no-store',headers:{apikey:key,Authorization:'Bearer '+(bearer||key),'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)}))}
export async function setSession(v:Row){if(!v.access_token||!v.refresh_token)fail(401,'Сессия недоступна');const jar=await cookies();const flags={httpOnly:true,secure:base().startsWith('https://'),sameSite:'lax' as const,path:'/'};jar.set('sf_access',v.access_token,{...flags,maxAge:Math.max(60,Number(v.expires_in)||3600)});jar.set('sf_refresh',v.refresh_token,{...flags,maxAge:60*60*24*30})}
export async function account(req:Request){
 const header=req.headers.get('authorization')||'';
 if(header.startsWith('Bearer sfp_')){
  const rows=await db('safar_api_keys?digest=eq.'+hash(header.slice(7))+'&select=owner');if(!rows.length)fail(401,'Неизвестный API-ключ');
  const info=await rpc('account',{u:rows[0].owner});if(!['pro','label'].includes(info.plan))fail(403,'Нужен Studio Pro');return {...info,viaKey:true}
 }
 const jar=await cookies();let access=header.startsWith('Bearer ')?header.slice(7):jar.get('sf_access')?.value;
 let user:Row|undefined;
 if(access){try{user=await authCall('user','GET',undefined,access)}catch(e){if(!(e instanceof Failure)||![401,403].includes(e.status))throw e}}
 if(!user){const refresh=jar.get('sf_refresh')?.value;if(!refresh)fail(401,'Войди в студию');const v=await authCall('token?grant_type=refresh_token','POST',{refresh_token:refresh});await setSession(v);user=v.user}
 return rpc('account',{u:uuid(user?.id)});
}
export async function track(owner:string,id:string){const r=await db('safar_tracks?id=eq.'+uuid(id)+'&owner=eq.'+uuid(owner)+'&limit=1');return r[0]||fail(404,'Сессия не найдена')}
export function mustOwner(a:Row){if(a.id!==a.owner||a.viaKey)fail(403,'Требуется вход владельца')}
export const products:Record<string,{price:number;subscription:boolean}>={creator:{price:1900,subscription:true},pro:{price:4900,subscription:true},label:{price:14900,subscription:true},top500:{price:1200,subscription:false},top2000:{price:3900,subscription:false},stems:{price:300,subscription:false},sync:{price:900,subscription:false},lora:{price:1900,subscription:false}};
