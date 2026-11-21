import {createHmac} from 'node:crypto';
import {base,db,env,fail,mustOwner,products,readResponse,rpc,secureEq,track,uuid,type Row} from './core';

const stripe=(path:string,form?:URLSearchParams,id?:string)=>fetch('https://api.stripe.com/v1/'+path,{method:form?'POST':'GET',cache:'no-store',headers:{Authorization:'Bearer '+env('STRIPE_SECRET_KEY'),'Stripe-Version':'2024-06-20',...(form?{'Content-Type':'application/x-www-form-urlencoded'}:{}),...(id?{'Idempotency-Key':id}:{})},body:form}).then(readResponse);
const lemon=(path:string,data?:Row)=>fetch('https://api.lemonsqueezy.com/v1/'+path,{method:data?'POST':'GET',cache:'no-store',headers:{Authorization:'Bearer '+env('LEMON_API_KEY'),Accept:'application/vnd.api+json','Content-Type':'application/vnd.api+json'},body:data?JSON.stringify(data):undefined}).then(readResponse);
function mapping(provider:string):Record<string,string>{return JSON.parse(env(provider==='stripe'?'STRIPE_PRICES_JSON':'LEMON_VARIANTS_JSON'))}
export async function checkout(a:Row,b:Row){
 mustOwner(a);const sku=String(b.sku),p=products[sku];if(!p)fail(422,'Выбери платный продукт');
 if(p.subscription&&a.plan!=='free')fail(409,'Для изменения активной подписки открой платёжный кабинет.');
 const id=uuid(b.id),target=['stems','sync'].includes(sku)?uuid(b.track_id):null;
 if(target){const t=await track(a.owner,target);if(t.state!=='committed'||!t.result?.delivery)fail(409,'Открой готовый трек с разделёнными стемами')}
 const provider=env('BILLING_PROVIDER');if(!['stripe','lemon'].includes(provider))fail(500,'Провайдер оплаты не настроен');
 const variant=mapping(provider)[sku]||fail(503,'Товар не настроен');
 await db('safar_intents?on_conflict=id','POST',{id,owner:a.owner,sku,track_id:target},'resolution=ignore-duplicates,return=minimal');
 const prev=(await db('safar_intents?id=eq.'+id))[0];if(prev.owner!==a.owner||prev.sku!==sku||prev.track_id!==target)fail(409,'Конфликт заказа');if(prev.url)return {url:prev.url};
 let url:string;
 if(provider==='stripe'){
  const form=new URLSearchParams({mode:p.subscription?'subscription':'payment','line_items[0][price]':variant,'line_items[0][quantity]':'1',customer_email:a.email,success_url:base()+'/?billing=success',cancel_url:base()+'/?billing=cancel','metadata[intent]':id,client_reference_id:id});
  form.set(p.subscription?'subscription_data[metadata][intent]':'payment_intent_data[metadata][intent]',id);
  url=(await stripe('checkout/sessions',form,'safar-'+id)).url;
 }else{
  const result=await lemon('checkouts',{data:{type:'checkouts',attributes:{test_mode:process.env.BILLING_MODE!=='live',product_options:{redirect_url:base()+'/?billing=success',enabled_variants:[Number(variant)]},checkout_data:{email:a.email,custom:{intent:id}}},relationships:{store:{data:{type:'stores',id:env('LEMON_STORE_ID')}},variant:{data:{type:'variants',id:String(variant)}}}}});
  url=result.data.attributes.url;
 }
 await db('safar_intents?id=eq.'+id,'PATCH',{url});return {url}
}
export async function portal(a:Row){mustOwner(a);const rows=await db('safar_subscriptions?owner=eq.'+a.owner+'&order=valid_until.desc&limit=1');if(!rows.length)fail(404,'Подписка не найдена');let s=rows[0];
 if(s.provider==='lemon')return {url:(await lemon('subscriptions/'+encodeURIComponent(s.remote_id))).data.attributes.urls.customer_portal};
 const sub=await stripe('subscriptions/'+encodeURIComponent(s.remote_id));
 return {url:(await stripe('billing_portal/sessions',new URLSearchParams({customer:sub.customer,return_url:base(),...(process.env.STRIPE_PORTAL_CONFIG?{configuration:process.env.STRIPE_PORTAL_CONFIG}:{})}))).url}
}
export function verify(provider:string,raw:string,sig:string,now=Date.now()/1000){
 if(provider==='stripe'){const parts=sig.split(',').map(s=>s.trim().split('=')),ts=parts.find(v=>v[0]==='t')?.[1];if(!ts||!/^\d+$/.test(ts)||Math.abs(now-Number(ts))>300)fail(401,'Недействительное время подписи');
 const mac=createHmac('sha256',env('STRIPE_WEBHOOK_SECRET')).update(ts+'.'+raw).digest('hex');
 if(!parts.some(([k,v])=>k==='v1'&&/^[a-f0-9]{64}$/.test(v)&&secureEq(v,mac)))fail(401,'Недействительная подпись')
 }else{const mac=createHmac('sha256',env('LEMON_WEBHOOK_SECRET')).update(raw).digest('hex');if(!/^[a-f0-9]{64}$/.test(sig)||!secureEq(sig,mac))fail(401,'Недействительная подпись')}
}
async function intent(id:unknown){const rows=await db('safar_intents?id=eq.'+uuid(id));return rows[0]||fail(409,'Заказ пока не зарегистрирован')}
async function paid(provider:string,ev:string,ref:string,it:Row,priceID:string,currency:string,cents:number,sub:string|null,end:string|null){
 const catalog=mapping(provider);const actual=Object.keys(catalog).find(k=>String(catalog[k])===String(priceID));
 if(actual&&products[it.sku]?.subscription&&products[actual]?.subscription)it={...it,sku:actual};
 if(currency.toUpperCase()!=='USD'||String(catalog[it.sku])!==String(priceID)||!Number.isInteger(cents)||cents<0)fail(422,'Данные оплаты не соответствуют заказу');
 if(cents===0)return;
 await rpc('payment',{ev:provider+':'+ev,receipt_id:provider+':'+ref,provider_in:provider,u:it.owner,product:it.sku,cents,target:it.track_id,sub_id:sub,ends:end});
}
const remote=(v:unknown)=>encodeURIComponent(String(v));
export async function webhook(provider:string,req:Request){
 if(!['stripe','lemon'].includes(provider))fail(404,'Неизвестный провайдер');
 const raw=await req.text();verify(provider,raw,req.headers.get(provider==='stripe'?'stripe-signature':'x-signature')||'');
 const evt=JSON.parse(raw),live=process.env.BILLING_MODE==='live';
 if(provider==='stripe'){
  if(evt.livemode!==live)fail(400,'Режим события отличается от режима магазина');
  const o=evt.data.object;
  if(evt.type==='checkout.session.completed'||evt.type==='checkout.session.async_payment_succeeded'){
   if(o.mode!=='payment'||o.payment_status!=='paid')return;
   const it=await intent(o.metadata.intent),items=await stripe('checkout/sessions/'+remote(o.id)+'/line_items');if(items.data.length!==1)fail(422,'Ожидалась одна позиция');
   await paid(provider,evt.id,o.payment_intent,it,items.data[0].price.id,o.currency,o.amount_subtotal,null,null)
  }else if(evt.type==='invoice.paid'){
   if(!o.subscription||o.paid_out_of_band||!['subscription_create','subscription_cycle'].includes(o.billing_reason))return;
   const sub=await stripe('subscriptions/'+remote(o.subscription)),it=await intent(sub.metadata.intent);
   const line=o.lines.data.find((l:Row)=>l.type==='subscription'&&!l.proration);if(!line)fail(422,'Нет строки оплаченного периода');
   await paid(provider,evt.id,o.id,it,line.price.id,o.currency,Math.min(o.amount_paid,o.subtotal_excluding_tax??o.subtotal),o.subscription,new Date(line.period.end*1000).toISOString());
  }else if(evt.type==='customer.subscription.updated'||evt.type==='customer.subscription.deleted'){
   const sub=await stripe('subscriptions/'+remote(o.id));
   await db('safar_subscriptions?provider=eq.stripe&remote_id=eq.'+remote(o.id),'PATCH',{renewing:!sub.cancel_at_period_end&&['active','trialing'].includes(sub.status),...(sub.status==='canceled'?{valid_until:new Date((sub.ended_at||sub.current_period_end)*1000).toISOString()}:{}),updated_at:new Date().toISOString()})
  }else if(evt.type==='charge.refunded'){
   const ref=o.invoice||o.payment_intent;
   await rpc('reverse',{ev:'stripe:'+evt.id,receipt_id:'stripe:'+ref,total_refunded:o.amount_refunded})
  }
 }else{
  const o=evt.data.attributes,id=String(evt.data.id),kind=String(evt.meta.event_name),key=kind+':'+id+':'+(o.updated_at||o.created_at);
  if(Boolean(o.test_mode??evt.meta.test_mode)!==!live)fail(400,'Режим магазина не совпадает');
  if(o.store_id&&String(o.store_id)!==env('LEMON_STORE_ID'))fail(403,'Неизвестный магазин');
  const custom=evt.meta.custom_data?.intent;
  if(kind==='order_created'){
   const sku=Object.keys(mapping(provider)).find(s=>String(mapping(provider)[s])===String(o.first_order_item?.variant_id));
   if(!sku||products[sku].subscription||o.status!=='paid')return;
   await paid(provider,key,'order-'+id,await intent(custom),o.first_order_item.variant_id,o.currency,o.subtotal,null,null);
  }else if(kind==='subscription_created'){
   const it=await intent(custom);if(String(mapping(provider)[it.sku])!==String(o.variant_id))fail(422,'Вариант подписки не совпадает');
   await db('safar_bindings?on_conflict=provider,remote_id','POST',{provider,remote_id:id,intent:it.id},'resolution=merge-duplicates,return=minimal');
  }else if(kind==='subscription_payment_success'){
   const subid=String(o.subscription_id),sub=(await lemon('subscriptions/'+remote(subid))).data.attributes;
   let cid=custom;if(!cid)cid=(await db('safar_bindings?provider=eq.lemon&remote_id=eq.'+remote(subid)))[0]?.intent;
   if(!cid)fail(503,'Ожидается событие привязки подписки');
   // Anchor the paid period to the invoice, including replay after a later renewal.
   const at=new Date(o.created_at),day=at.getUTCDate();at.setUTCDate(1);at.setUTCMonth(at.getUTCMonth()+1);const last=new Date(Date.UTC(at.getUTCFullYear(),at.getUTCMonth()+1,0)).getUTCDate();at.setUTCDate(Math.min(day,last));
   await paid(provider,key,'invoice-'+id,await intent(cid),sub.variant_id,o.currency,o.subtotal,subid,at.toISOString());
  }else if(['subscription_cancelled','subscription_resumed','subscription_expired','subscription_updated'].includes(kind)){
   const sub=(await lemon('subscriptions/'+remote(id))).data.attributes;
   await db('safar_subscriptions?provider=eq.lemon&remote_id=eq.'+remote(id),'PATCH',{renewing:!sub.cancelled&&sub.status==='active',...(sub.status==='expired'?{valid_until:sub.ends_at||new Date().toISOString()}:{}),updated_at:new Date().toISOString()});
  }else if(['order_refunded','subscription_payment_refunded'].includes(kind)){
   if(kind==='order_refunded'&&Object.entries(mapping(provider)).some(([s,v])=>products[s].subscription&&String(v)===String(o.first_order_item?.variant_id)))return;
   const amount=o.refunded_amount??(o.refunded?o.total:0);
   if(amount>0)await rpc('reverse',{ev:'lemon:'+key,receipt_id:'lemon:'+(kind==='order_refunded'?'order-':'invoice-')+id,total_refunded:amount});
  }
 }
}
