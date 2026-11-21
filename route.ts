import {cookies} from 'next/headers';
import {account,authCall,base,db,env,fail,Failure,gpu,hash,mustOwner,rpc,secureEq,setSession,ticket,token,track,uuid,type Row} from '@/lib/core';
import {checkout,portal,webhook} from '@/lib/billing';
import {completionMac,download,reconcile,submit,summary,sweep} from '@/lib/jobs';
import {certificate} from '@/lib/license';
export const runtime='nodejs';export const dynamic='force-dynamic';export const maxDuration=120;
type Params={params:Promise<{path:string[]}>};
async function handle(req:Request,ctx:Params){try{
 const parts=(await ctx.params).path,path=parts.join('/'),method=req.method;
 if(path.startsWith('webhooks/')&&method==='POST'){await webhook(parts[1],req);return Response.json({received:true})}
 if(path==='cron/reconcile'&&method==='GET'){
  if(!secureEq(req.headers.get('authorization')||'','Bearer '+env('CRON_SECRET')))fail(401,'Не авторизовано');return Response.json(await sweep())
 }
 if(method==='POST'&&req.headers.get('origin')&&req.headers.get('origin')!==base())fail(403,'Другой origin');
 const body:Row=method==='POST'?await req.json():{};
 if(path==='jobs/notify'&&method==='POST'){const u=uuid(body.owner),id=uuid(body.id);if(!secureEq(req.headers.get('x-safar-notify')||'',completionMac(u,id)))fail(401,'Недействительная подпись уведомления');await reconcile(await track(u,id));return Response.json({ok:true})}
 if(path==='auth/start'&&method==='POST'){
  if(typeof body.email!=='string'||body.email.length>254||!/^\S+@\S+\.\S+$/.test(body.email))fail(422,'Проверь адрес');
  await authCall('otp?redirect_to='+encodeURIComponent(base()+'/auth/callback'),'POST',{email:body.email,create_user:true});return Response.json({sent:true})
 }
 if(path==='auth/session'&&method==='POST'){
  const v=await authCall('token?grant_type=refresh_token','POST',{refresh_token:body.refresh_token});await setSession(v);return Response.json({ok:true})
 }
 if(path==='auth/verify'&&method==='POST'){await setSession(await authCall('verify','POST',{email:body.email,token:body.code,type:'email'}));return Response.json({ok:true})}
 if(path==='auth/logout'&&method==='POST'){const jar=await cookies();const access=jar.get('sf_access')?.value;if(access)await authCall('logout?scope=local','POST',{},access).catch(()=>null);jar.delete('sf_access');jar.delete('sf_refresh');return Response.json({ok:true})}
 if(parts[0]==='listen'&&method==='GET'){
  if(!/^[\w-]{32}$/.test(parts[1]||''))fail(404,'Ссылка не найдена');
  const rows=await db('safar_shares?token=eq.'+parts[1]+'&limit=1'),s=rows[0];if(!s||Date.parse(s.expires_at)<Date.now())fail(404,'Ссылка закрыта');
  const plan=await rpc('plan',{u:s.owner});if(!['pro','label'].includes(plan))fail(403,'Ссылка приостановлена');
  return Response.json({brand:plan==='label'?s.brand:'',sources:await Promise.all([s.a,s.b].map(id=>ticket(s.owner,id,'Master.mp3')))})
 }
 const a=await account(req),out=(v:unknown)=>Response.json(v);
 if(path==='me'&&method==='GET')return out(a);
 if(path==='refer'&&method==='POST'){await rpc('attach_ref',{u:a.id,r:uuid(body.ref)});return out({ok:true})}
 if(path==='checkout'&&method==='POST')return out(await checkout(a,body));
 if(path==='billing/portal'&&method==='POST')return out(await portal(a));
 if(path==='uploads'&&method==='POST'){const t=await gpu('/api/tickets',{op:'upload',owner:a.owner});return out({id:t.id,url:env('MODAL_URL').replace(/\/$/,'')+t.path})}
 if(path==='jobs'&&method==='POST')return Response.json(await submit(a,body),{status:202});
 if(path==='jobs'&&method==='GET'){const r=await db('safar_tracks?owner=eq.'+a.owner+'&order=created_at.desc&limit=100');return out(r.map(summary))}
 if(parts[0]==='jobs'&&parts.length===2&&method==='GET')return out(summary(await reconcile(await track(a.owner,parts[1]))));
 if(path==='batch'&&method==='POST'){if(a.plan!=='label')fail(403,'Нужен Label');if(!Array.isArray(body.jobs)||!body.jobs.length||body.jobs.length>10)fail(422,'В пакете 1–10 заданий');
  const res=[];for(const job of body.jobs){try{res.push(await submit(a,job))}catch(e){res.push({id:job.id,error:e instanceof Error?e.message:'Ошибка'})}}return out(res)}
 if(parts[0]==='files'&&parts.length===3&&method==='GET')return out(await download(a,parts[1],parts[2]));
 if(parts[0]==='license'&&method==='GET'){const bytes=await certificate(a.owner,parts[1]);return new Response(new Blob([bytes as BlobPart],{type:'application/pdf'}),{headers:{'Content-Disposition':'attachment; filename="SAFAR_License.pdf"','Content-Type':'application/pdf'}})}
 if(path==='keys'&&method==='POST'){mustOwner(a);if(!['pro','label'].includes(a.plan))fail(403,'Нужен Studio Pro');const secret='sfp_'+token();await db('safar_api_keys','POST',{owner:a.owner,digest:hash(secret)});return out({key:secret})}
 if(path==='keys/revoke'&&method==='POST'){mustOwner(a);await db('safar_api_keys?owner=eq.'+a.owner,'DELETE');return out({ok:true})}
 if(path==='team'&&method==='POST'){mustOwner(a);await rpc('add_seat',{u:a.id,who:String(body.email).trim().toLowerCase()});return out({ok:true})}
 if(path==='team/remove'&&method==='POST'){mustOwner(a);await db('safar_seats?owner=eq.'+a.owner+'&member=eq.'+uuid(body.member),'DELETE');return out({ok:true})}
 if(path==='team'&&method==='GET'){mustOwner(a);return out(await db('safar_seats?owner=eq.'+a.owner+'&select=member'))}
 if(path==='share'&&method==='POST'){if(!['pro','label'].includes(a.plan))fail(403,'Нужен Studio Pro');const tracks=await Promise.all([body.a,body.b].map(id=>track(a.owner,id)));if(tracks.some(t=>!['generate','repaint'].includes(t.kind)||t.state!=='committed'))fail(422,'Выбери два готовых трека');
  const value=token();await db('safar_shares','POST',{token:value,owner:a.owner,a:tracks[0].id,b:tracks[1].id,brand:a.plan==='label'?String(body.brand||'').slice(0,64):''});return out({url:base()+'/ab/'+value})}
 if(path==='share/revoke'&&method==='POST'){const t=String(body.token||'');if(!/^[\w-]{32}$/.test(t))fail(422,'Неверный токен');await db('safar_shares?owner=eq.'+a.owner+'&token=eq.'+t,'DELETE');return out({ok:true})}
 if(path==='economics'){if(!a.admin||a.viaKey)fail(403,'Доступ владельца');if(method==='POST'){if(!['free','creator','pro','label'].includes(body.plan)||!/^\d{4}-\d{2}-01$/.test(body.month))fail(422,'Проверь месяц и тариф');const v:Row={month:body.month,plan:body.plan};for(const k of ['gpu_usd','fee_usd','other_usd']){const n=Number(body[k]);if(!Number.isFinite(n)||n<0)fail(422,'Проверь расходы');v[k]=n}await db('safar_costs?on_conflict=month,plan','POST',v,'resolution=merge-duplicates,return=minimal')}return out(await rpc('economics',{}))}
 return fail(404,'Маршрут не найден')
 }catch(e){let status=e instanceof Failure?e.status:500;let message=e instanceof Error?e.message:'Ошибка студии';if(/INSUFFICIENT_CREDITS|LORA_PASS_REQUIRED|UPGRADE_REQUIRED/.test(message))status=402;if(/IDEMPOTENCY_CONFLICT|TEAM_FULL/.test(message))status=409;if(e instanceof SyntaxError){status=422;message='Некорректный JSON'}return Response.json({error:message},{status:status>=400&&status<=599?status:500})}}
export {handle as GET,handle as POST};
