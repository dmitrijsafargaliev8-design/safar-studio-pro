import {createHmac} from 'node:crypto';
import {base,db,env,fail,Failure,gpu,rpc,ticket,track,uuid,type Row} from './core';
export const stems=['vocals','drums','bass','guitar','piano','other'];
const clip=(v:unknown,n:number)=>String(v??'').slice(0,n);
function number(v:unknown,d:number,lo:number,hi:number){const n=v===undefined?d:Number(v);if(!Number.isFinite(n)||n<lo||n>hi)fail(422,'Значение за пределами диапазона');return n}
function option(v:unknown,list:string[],fallback:string){const s=String(v??fallback);if(!list.includes(s))fail(422,'Недоступный режим');return s}
export function normalize(b:Row){
 const k=option(b.kind,['generate','repaint','train','review'],'generate'),doc:Row={kind:k,
 name:clip(b.name||b.brief||'SAFAR Track',80),brief:clip(b.brief,3000),lyrics:clip(b.lyrics,16000),profile:clip(b.profile,200),voice:clip(b.voice,150),language:clip(b.language||'ru',3),key:clip(b.key||'Dm',24),
 bpm:Math.round(number(b.bpm,88,30,300)),seed:Math.round(number(b.seed,2026,0,2147483647)),duration_sec:number(b.duration_sec,40,12,600),
 model:option(b.model,['sketch','studio'],'sketch'),preset:option(b.preset,['streaming','club','apple'],'streaming'),
 mode:option(b.mode,['instrumental','song_with_vocals','cover','style_transfer','vocal_to_bgm'],'song_with_vocals'),split_stems:true};
 if(!/^[a-z]{2,3}$/.test(doc.language))fail(422,'Проверь язык');
 for(const key of ['parent_id','upload_id','lora_id'])if(b[key])doc[key]=uuid(b[key]);
 if(k==='repaint'){doc.start_sec=number(b.start_sec,0,0,600);doc.end_sec=number(b.end_sec,0,0,600);doc.instruction=clip(b.instruction,2000);if(doc.end_sec<=doc.start_sec)fail(422,'Пустой интервал')}
 if((k==='repaint'||['cover','style_transfer','vocal_to_bgm'].includes(doc.mode))&&Number(!!doc.parent_id)+Number(!!doc.upload_id)!==1)fail(422,'Выбери один исходник');
 if(k==='train'){if(!Array.isArray(b.upload_ids)||b.upload_ids.length<5||b.upload_ids.length>10||b.rights_confirmed!==true)fail(422,'Нужны 5–10 собственных записей и подтверждение прав');doc.upload_ids=b.upload_ids.map(uuid);if(new Set(doc.upload_ids).size!==doc.upload_ids.length)fail(422,'Записи должны различаться');doc.rights_confirmed=true;doc.epochs=Math.round(number(b.epochs,50,1,200))}
 if(b.clean_intervals){if(!Array.isArray(b.clean_intervals)||b.clean_intervals.length>256)fail(422,'Неверный список Clean');doc.clean_intervals=b.clean_intervals.map((c:unknown[])=>{if(!Array.isArray(c)||c.length!==2)fail(422,'Нужен интервал');const a=number(c[0],0,0,600),z=number(c[1],0,0,600);if(z<=a)fail(422,'Пустой интервал');return [a,z]})}
 return doc;
}
export const completionMac=(owner:string,id:string)=>createHmac('sha256',env('SAFAR_GPU_TOKEN')).update('notify:'+owner+':'+id).digest('hex');
async function dispatch(t:Row){const url=base();try{await gpu('/api/jobs',{...t.payload,id:t.id,owner:t.owner,...(url.startsWith('https://')?{completion_url:url+'/api/jobs/notify',completion_mac:completionMac(t.owner,t.id)}:{}),priority:['pro','label'].includes(t.plan)})}catch(e){
 if(e instanceof Failure&&[400,404,409,422].includes(e.status)){await rpc('finish',{j:t.id,ok:false,report:{error:e.message}});throw e}
 // Uncertain HTTP delivery is reconciled by the same job UUID.
 }}
export async function submit(a:Row,b:Row){const id=uuid(b.id),p=normalize(b);
 const t=await rpc('reserve',{u:a.id,j:id,k:p.kind,payload_in:p});if(t.state==='reserved')await dispatch(t);return {id,state:t.state}}
export async function reconcile(t:Row){
 if(t.state!=='reserved')return t;
 let status:Row;
 try{status=await gpu('/api/jobs/'+t.id)}catch(e){
  if(e instanceof Failure&&e.status===404){
   if(Date.now()-Date.parse(t.created_at)>5*3600e3){await rpc('finish',{j:t.id,ok:false,report:{error:'Истёк срок доставки задачи'}});return track(t.owner,t.id)}
   await dispatch(t);
  }else if(!(e instanceof Failure)||e.status>=500)return t;else throw e;
  return t
 }
 if(status.owner!==t.owner)fail(409,'Конфликт владельца задания');
 if(['done','failed'].includes(status.state)){await rpc('finish',{j:t.id,ok:status.state==='done',report:status});return track(t.owner,t.id)}
 if(status.stage!==t.stage){await db('safar_tracks?id=eq.'+t.id+'&state=eq.reserved','PATCH',{stage:status.stage,updated_at:new Date().toISOString()});t.stage=status.stage}
 return t
}
export function summary(t:Row){return {id:t.id,title:t.title,kind:t.kind,state:t.state,stage:t.stage,model:t.payload?.model,created_at:t.created_at,bpm:t.payload?.bpm,error:t.error,telemetry:t.result?.telemetry,outside_max_abs_diff:t.result?.outside_max_abs_diff,separator:t.result?.separator,delivery:t.result?.delivery,review:t.result?.review}}
export async function download(a:Row,id:string,file:string){
 const t=await track(a.owner,id);if(t.state!=='committed'||!['generate','repaint'].includes(t.kind))fail(409,'Аудио пока недоступно');
 const always=['words.json','Master.mp3',...stems.map(s=>s+'-preview.mp3')];
 const paid=t.plan!=='free'||a.plan!=='free';let allowed=always.includes(file)||(paid&&file==='Master.wav');
 const bundle=['Stems.zip',...stems.map(s=>s+'.wav')].includes(file)?'stems':['SyncPack.zip','TV_Mix.wav','Instrumental.wav','Clean.wav'].includes(file)?'sync':'';
 if(bundle){allowed=bundle==='stems'?paid:['pro','label'].includes(a.plan)||['pro','label'].includes(t.plan);
  if(!allowed){const rows=await db('safar_grants?owner=eq.'+a.owner+'&track_id=eq.'+id+'&sku=eq.'+bundle+'&revoked=eq.false&select=id');allowed=!!rows.length}}
 if(!allowed)fail(403,'Формат доступен в другом тарифе или как разовая покупка');
 return {url:await ticket(a.owner,id,file)}
}
export async function sweep(){const list=await db('safar_tracks?state=eq.reserved&select=*&order=updated_at.asc&limit=24');
 let processed=0;for(let i=0;i<list.length;i+=4){await Promise.all(list.slice(i,i+4).map(async(t:Row)=>{await reconcile(t);await db('safar_tracks?id=eq.'+t.id+'&state=eq.reserved','PATCH',{updated_at:new Date().toISOString()});processed++}))}return {processed}}
