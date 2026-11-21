import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
const key=process.env.STRIPE_SECRET_KEY,origin=(process.env.APP_URL||'').replace(/\/$/,'');
if(!key||!/^https:\/\//.test(origin))throw Error('Нужны STRIPE_SECRET_KEY и внешний HTTPS APP_URL в окружении');
const mode=key.startsWith('sk_test_')||key.startsWith('rk_test_')?'test':'live';
if(mode==='live'&&!process.argv.includes('--live'))throw Error('Для каталога боевого магазина запусти с --live');
const all=[['creator','Creator',1900,true],['pro','Studio Pro',4900,true],['label','Label / Agency',14900,true],['top500','500 credits',1200,false],['top2000','2000 credits',3900,false],['stems','6-Stem Pack',300,false],['sync','4-WAV Sync Pack',900,false],['lora','Artist LoRA',1900,false]];
async function call(path,form,id){const r=await fetch('https://api.stripe.com/v1/'+path,{method:form?'POST':'GET',headers:{Authorization:'Bearer '+key,'Stripe-Version':'2024-06-20',...(id?{'Idempotency-Key':id}:{}),...(form?{'Content-Type':'application/x-www-form-urlencoded'}:{})},body:form?new URLSearchParams(form):undefined});const j=await r.json();if(!r.ok)throw Error(j.error?.message||String(r.status));return j}
const prices={};
for(const [sku,name,cents,monthly] of all){
 const tag='safar_v5_'+sku;const found=(await call('prices?active=true&lookup_keys[]='+tag)).data[0];
 if(found){if(found.unit_amount!==cents||found.currency!=='usd'||Boolean(found.recurring)!==monthly)throw Error('Параметры цены отличаются: '+tag);prices[sku]=found.id;continue}
 let prod;try{prod=await call('products/'+tag)}catch(e){if(!String(e.message).includes('No such product'))throw e;prod=await call('products',{id:tag,name:'SAFAR · '+name,'metadata[sku]':sku},tag+'-product')}
 const price=await call('prices',{product:prod.id,currency:'usd',unit_amount:String(cents),lookup_key:tag,...(monthly?{'recurring[interval]':'month'}:{})},tag+'-price');prices[sku]=price.id;
}
const dir=resolve('.safar-private');await mkdir(dir,{recursive:true,mode:0o700});
const url=origin+'/api/webhooks/stripe',scope=createHash('sha256').update(url).digest('hex').slice(0,14);
const path=dir+'/stripe-'+mode+'-'+scope+'.env';let saved=existsSync(path)?await readFile(path,'utf8'):'';
let secret=saved.match(/^STRIPE_WEBHOOK_SECRET=(.+)$/m)?.[1]||process.env.STRIPE_WEBHOOK_SECRET;
const hooks=(await call('webhook_endpoints?limit=100')).data;let hook=hooks.find(h=>h.url===url&&h.status==='enabled');
if(!hook){const events=['checkout.session.completed','checkout.session.async_payment_succeeded','invoice.paid','customer.subscription.updated','customer.subscription.deleted','charge.refunded'];
 const params={url,api_version:'2024-06-20'};events.forEach((e,i)=>params['enabled_events['+i+']']=e);
 hook=await call('webhook_endpoints',params,'safar-v5-hook-'+scope);secret=hook.secret;
}else if(!secret){throw Error('Webhook уже существует. Передай его signing secret из Stripe в STRIPE_WEBHOOK_SECRET и повтори.')}
const configs=(await call('billing_portal/configurations?limit=100')).data;let portal=configs.find(c=>c.active&&c.metadata?.safar==='v5');
if(!portal)portal=await call('billing_portal/configurations',{'metadata[safar]':'v5','business_profile[headline]':'SAFAR STUDIO','features[subscription_cancel][enabled]':'true','features[subscription_cancel][mode]':'at_period_end','features[payment_method_update][enabled]':'true','features[invoice_history][enabled]':'true'},'safar-v5-portal');
await writeFile(path,['BILLING_PROVIDER=stripe','STRIPE_PORTAL_CONFIG='+portal.id,'BILLING_MODE='+mode,'STRIPE_SECRET_KEY='+key,'STRIPE_WEBHOOK_SECRET='+secret,'STRIPE_PRICES_JSON='+JSON.stringify(prices),''].join('\n'),{mode:0o600});
console.log('Каталог и webhook готовы. Приватные настройки: '+path);
