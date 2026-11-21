import assert from 'node:assert/strict';
import {createHmac,randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import ts from 'typescript';
import {PGlite} from '@electric-sql/pglite';
const cache=resolve('node_modules/.cache');await mkdir(cache,{recursive:true});const tmp=await mkdtemp(cache+'/safar-billing-');
const pg=new PGlite(),oldFetch=globalThis.fetch;
try{
 for(const path of ['core','billing']){const src=await readFile('lib/'+path+'.ts','utf8');const {outputText}=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}});await writeFile(tmp+'/'+path+'.js',outputText)}
 const {verify,webhook}=createRequire(import.meta.url)(tmp+'/billing.js');
 Object.assign(process.env,{BILLING_MODE:'test',STRIPE_SECRET_KEY:'test-only',LEMON_API_KEY:'test-only',LEMON_STORE_ID:'21',STRIPE_WEBHOOK_SECRET:'radio-test',LEMON_WEBHOOK_SECRET:'analog-test',SUPABASE_URL:'https://db.example.test',SUPABASE_SERVICE_ROLE_KEY:'test-only',STRIPE_PRICES_JSON:'{"top500":"price_500","pro":"price_pro"}',LEMON_VARIANTS_JSON:'{"top500":"501","pro":"502"}'});
 await pg.exec('create role anon;create role authenticated;create role service_role bypassrls;create schema auth;create table auth.users(id uuid primary key,email text)');
 await pg.exec(await readFile('supabase/001_studio.sql','utf8'));
 const user=randomUUID(),order=randomUUID(),sub=randomUUID();
 await pg.query('insert into auth.users values($1,$2)',[user,'label@example.test']);
 await pg.query("insert into safar_intents(id,owner,sku) values($1,$3,'top500'),($2,$3,'pro')",[order,sub,user]);
 const credits=async()=>(await pg.query('select credits from safar_users where id=$1',[user])).rows[0].credits;
 globalThis.fetch=async(input,config={})=>{const u=new URL(input);let res;
  if(u.hostname==='db.example.test'){
   if(u.pathname.endsWith('/safar_intents'))res=(await pg.query('select * from safar_intents where id=$1',[u.searchParams.get('id').slice(3)])).rows;
   else if(u.pathname.endsWith('/rpc/safar_payment')){const j=JSON.parse(config.body);await pg.query('select safar_payment($1,$2,$3,$4,$5,$6,$7,$8,$9)',[j.ev,j.receipt_id,j.provider_in,j.u,j.product,j.cents,j.target,j.sub_id,j.ends]);res=null}
   else if(u.pathname.endsWith('/rpc/safar_reverse')){const j=JSON.parse(config.body);await pg.query('select safar_reverse($1,$2,$3)',[j.ev,j.receipt_id,j.total_refunded]);res=null}
   else throw Error('Unexpected database path '+u.pathname)
  }else if(u.pathname.endsWith('/line_items'))res={data:[{price:{id:'price_500'}}]};
  else if(u.hostname==='api.stripe.com'&&u.pathname.includes('/subscriptions/'))res={metadata:{intent:sub}};
  else if(u.hostname==='api.lemonsqueezy.com'&&u.pathname.includes('/subscriptions/'))res={data:{attributes:{variant_id:502}}};
  else throw Error('Unexpected provider path '+u.pathname);
  return Response.json(res)
 };
 const sign=(secret,p)=>createHmac('sha256',secret).update(p).digest('hex');
 async function send(provider,obj){const raw=JSON.stringify(obj),now=Math.floor(Date.now()/1000);const h=provider==='stripe'?{'stripe-signature':'t='+now+',v1='+sign('radio-test',now+'.'+raw)}:{'x-signature':sign('analog-test',raw)};await webhook(provider,new Request('https://test.invalid',{method:'POST',headers:h,body:raw}))}
 const stripe={id:'evt_one',livemode:false,type:'checkout.session.completed',data:{object:{id:'cs_one',payment_intent:'pi_one',mode:'payment',payment_status:'paid',amount_subtotal:1200,currency:'usd',metadata:{intent:order}}}};
 await send('stripe',stripe);await send('stripe',stripe);assert.equal(await credits(),530);
 const invoice={id:'iv1',livemode:false,type:'invoice.paid',data:{object:{id:'in_1',subscription:'s1',billing_reason:'subscription_cycle',amount_paid:4900,subtotal:4900,currency:'usd',lines:{data:[{type:'subscription',price:{id:'price_pro'},period:{end:Math.floor(Date.now()/1000)+30*86400}}]}}}};
 await send('stripe',invoice);assert.equal(await credits(),2530);
 const lemon={meta:{event_name:'order_created',custom_data:{intent:order}},data:{id:'l1',attributes:{test_mode:true,store_id:21,status:'paid',subtotal:1200,currency:'USD',first_order_item:{variant_id:501},created_at:'2026-10-20'}}};
 await send('lemon',lemon);await send('lemon',lemon);assert.equal(await credits(),3030);
 await send('lemon',{meta:{event_name:'subscription_payment_success',custom_data:{intent:sub}},data:{id:'invL',attributes:{test_mode:true,subscription_id:'L2',created_at:'2026-10-31T12:00:00Z',currency:'USD',subtotal:4900}}});assert.equal(await credits(),5030);
 await send('stripe',{type:'charge.refunded',livemode:false,id:'refund',data:{object:{payment_intent:'pi_one',amount_refunded:1200}}});assert.equal(await credits(),4530);
 assert.throws(()=>verify('lemon','changed',sign('analog-test','original')),/подпись/);assert.throws(()=>verify('stripe','data','t=1,v1=aa'),/время/);
 console.log('PASS · signed Stripe and Lemon fixtures through PostgreSQL, recurring invoices, duplicate events, credit refund, timestamp and signature rejection');
}finally{globalThis.fetch=oldFetch;await pg.close();await rm(tmp,{recursive:true,force:true})}
