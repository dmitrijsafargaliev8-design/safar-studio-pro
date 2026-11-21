begin;
create table public.safar_users(
 id uuid primary key references auth.users(id) on delete cascade, email text not null,
 credits integer not null default 30, referrer uuid references public.safar_users(id),
 first_paid_at timestamptz, is_admin boolean not null default false, created_at timestamptz default now(),
 free_month date not null default date_trunc('month',now() at time zone 'UTC')::date,
 check(referrer is distinct from id));
create table public.safar_catalog(sku text primary key, price_cents integer not null, credits integer not null, recurring boolean not null, seats integer not null default 1);
insert into public.safar_catalog values ('free',0,30,true,1),('creator',1900,600,true,1),('pro',4900,2000,true,1),('label',14900,8000,true,5),('top500',1200,500,false,1),('top2000',3900,2000,false,1),('stems',300,0,false,1),('sync',900,0,false,1),('lora',1900,0,false,1);
create table public.safar_tracks(id uuid primary key,owner uuid not null references public.safar_users(id), actor uuid not null references public.safar_users(id),
 kind text not null check(kind in ('generate','repaint','train','review')), title text not null, plan text not null references public.safar_catalog,
 state text not null check(state in ('reserved','committed','refunded')), amount integer not null, payload jsonb not null,
 result jsonb, error text, stage text not null default 'Queued', gpu_seconds numeric not null default 0, gpu text,
 gpu_usd numeric not null default 0, created_at timestamptz not null default now(), updated_at timestamptz not null default now());
create index safar_pending on public.safar_tracks(created_at) where state='reserved';
create table public.safar_ledger(id bigint generated always as identity primary key,owner uuid not null references public.safar_users(id),kind text not null,
 delta integer not null, balance_after integer not null, reference text not null, created_at timestamptz not null default now(),unique(owner,reference));
create table public.safar_subscriptions(provider text not null, remote_id text not null,owner uuid not null references public.safar_users(id),
 sku text not null references public.safar_catalog, valid_until timestamptz not null,renewing boolean not null default true,
 updated_at timestamptz not null default now(),primary key(provider,remote_id));
create table public.safar_payments(receipt text primary key,provider text not null,owner uuid not null references public.safar_users(id),sku text not null references public.safar_catalog,
 track_id uuid references public.safar_tracks(id),subtotal_cents integer not null,refunded_cents integer not null default 0,
 usd_fee_cents integer,subscription_id text,period_end timestamptz,created_at timestamptz not null default now());
create table public.safar_events(event_id text primary key,created_at timestamptz not null default now());
create table public.safar_grants(id uuid primary key default gen_random_uuid(),owner uuid not null references public.safar_users(id),sku text not null,track_id uuid references public.safar_tracks(id),receipt text unique references public.safar_payments,consumed_by uuid references public.safar_tracks,revoked boolean not null default false);
create table public.safar_licenses(track_id uuid primary key references public.safar_tracks, owner uuid not null references public.safar_users, issued_at timestamptz default now(),
 holder text not null, wav_sha256 text not null check(length(wav_sha256)=64),model text not null,revision text not null);
create table public.safar_referrals(referred uuid primary key references public.safar_users, referrer uuid not null references public.safar_users,receipt text not null references public.safar_payments,reversed boolean not null default false);
create table public.safar_seats(member uuid primary key references public.safar_users, owner uuid not null references public.safar_users,check(member<>owner));
create table public.safar_api_keys(digest text primary key,owner uuid not null references public.safar_users,created_at timestamptz default now());
create table public.safar_shares(token text primary key,owner uuid not null references public.safar_users,a uuid not null references public.safar_tracks,b uuid not null references public.safar_tracks,brand text not null default '',expires_at timestamptz not null default now()+interval '30 days',check(a<>b));
create table public.safar_costs(month date not null,plan text not null references public.safar_catalog,fee_usd numeric not null check(fee_usd>=0),gpu_usd numeric not null check(gpu_usd>=0),other_usd numeric not null check(other_usd>=0),primary key(month,plan));
create table public.safar_intents(id uuid primary key,owner uuid not null references public.safar_users,sku text not null references public.safar_catalog,track_id uuid references public.safar_tracks,url text,created_at timestamptz default now());
create table public.safar_bindings(provider text not null,remote_id text not null,intent uuid not null references public.safar_intents,primary key(provider,remote_id));
create function public.safar_credit(u uuid,d integer,k text,r text) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare balance integer;
begin
 perform 1 from safar_users where id=u for update;
 if exists(select 1 from safar_ledger where owner=u and reference=r) then return; end if;
 update safar_users set credits=credits+d where id=u returning credits into strict balance;
 insert into safar_ledger(owner,kind,delta,balance_after,reference) values(u,k,d,balance,r);
end $$;
create function public.safar_on_signup() returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
 insert into safar_users(id,email) values(new.id,coalesce(new.email,'')) on conflict do nothing;
 insert into safar_ledger(owner,kind,delta,balance_after,reference) values(new.id,'signup',30,30,'signup') on conflict do nothing;
 return new;
end $$;
create trigger safar_signup after insert on auth.users for each row execute function public.safar_on_signup();
insert into public.safar_users(id,email) select id,coalesce(email,'') from auth.users on conflict do nothing;
insert into public.safar_ledger(owner,kind,delta,balance_after,reference) select id,'signup',30,30,'signup' from public.safar_users on conflict do nothing;
create function public.safar_plan(u uuid) returns text language sql stable set search_path=public,pg_temp as $$
 select coalesce((select s.sku from safar_subscriptions s join safar_catalog c on c.sku=s.sku where s.owner=u and valid_until>now() order by c.price_cents desc limit 1),'free') $$;
create function public.safar_workspace(u uuid) returns uuid language sql stable set search_path=public,pg_temp as $$
 select coalesce((select owner from safar_seats where member=u and safar_plan(owner)='label'),u) $$;
create function public.safar_account(u uuid) returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare w uuid:=safar_workspace(u); p text:=safar_plan(w); m date:=date_trunc('month',now() at time zone 'UTC')::date; x safar_users;
begin
 select * into strict x from safar_users where id=w for update;
 if p='free' and x.free_month<m then
 perform safar_credit(w,30,'monthly_free','free:'||m); update safar_users set free_month=m where id=w;
 end if;
 return (select jsonb_build_object('owner',w,'id',u,'credits',credits,'plan',p,'admin',(select is_admin from safar_users where id=u),'email',(select email from safar_users where id=u)) from safar_users where id=w);
end $$;
create function public.safar_reserve(u uuid,j uuid,k text,payload_in jsonb) returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare w uuid:=safar_workspace(u); p text; bal integer; old safar_tracks; price integer:=10; months_used integer; one_grant uuid;
begin
 perform safar_account(u);
 select credits into strict bal from safar_users where id=w for update;
 p:=safar_plan(w);
 select * into old from safar_tracks where id=j;
 if found then
 if old.owner<>w or old.actor<>u or old.payload<>payload_in or old.kind<>k then raise exception 'IDEMPOTENCY_CONFLICT';end if;
 return to_jsonb(old);end if;
 if k not in ('generate','repaint','train','review') then raise exception 'INVALID_KIND';end if;
 if p='free' and (k='generate' and payload_in->>'model'='studio' or k='repaint') then raise exception 'UPGRADE_REQUIRED';end if;
 if k='review' then price:=1;end if;
 if k='train' then
 select count(*) into months_used from safar_tracks where owner=w and kind='train' and state<>'refunded' and created_at>=date_trunc('month',now() at time zone 'UTC') at time zone 'UTC' and not exists(select 1 from safar_grants where consumed_by=safar_tracks.id);
 if p<>'label' and (p<>'pro' or months_used>=1) then
 select id into one_grant from safar_grants where owner=w and sku='lora' and consumed_by is null and not revoked limit 1 for update;
 if one_grant is null then raise exception 'LORA_PASS_REQUIRED';end if;end if;
 price:=0;
 end if;
 if bal<price then raise exception 'INSUFFICIENT_CREDITS';end if;
 if payload_in ? 'parent_id' and not exists(select 1 from safar_tracks where id=(payload_in->>'parent_id')::uuid and owner=w and state='committed' and kind in ('generate','repaint')) then raise exception 'SOURCE_MISSING';end if;
 if payload_in ? 'lora_id' and not exists(select 1 from safar_tracks where id=(payload_in->>'lora_id')::uuid and owner=w and state='committed' and kind='train') then raise exception 'LORA_MISSING';end if;
 insert into safar_tracks(id,owner,actor,kind,title,plan,state,amount,payload) values(j,w,u,k,left(coalesce(nullif(payload_in->>'name',''),nullif(payload_in->>'brief',''),'SAFAR Track'),90),p,'reserved',price,payload_in) returning * into old;
 update safar_grants set consumed_by=j where id=one_grant;
 perform safar_credit(w,-price,'reserve','reserve:'||j);
 return to_jsonb(old);
end $$;
create function public.safar_finish(j uuid,ok boolean,report jsonb) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare t safar_tracks; res jsonb:=report->'result'; seconds numeric:=greatest(coalesce((report->>'gpu_seconds')::numeric,0),0);
begin
 select * into strict t from safar_tracks where id=j for update;
 if t.state<>'reserved' then return;end if;
 update safar_tracks set state=case when ok then 'committed' else 'refunded' end,result=res,stage=case when ok then 'Complete' else 'Failed' end,
 error=report->>'error',gpu=report->>'gpu',gpu_seconds=seconds,gpu_usd=seconds*case when report->>'gpu'='L40S' then .000542 else .000222 end,updated_at=now() where id=j;
 perform safar_credit(t.owner,case when ok then 0 else t.amount end,case when ok then 'commit' else 'refund' end,'finish:'||j);
 if not ok then update safar_grants set consumed_by=null where consumed_by=j;end if;
 if ok and t.plan<>'free' and t.kind in ('generate','repaint') and res->>'model_license'='MIT' and length(res#>>'{telemetry,wav_sha256}')=64 then
 insert into safar_licenses(track_id,owner,holder,wav_sha256,model,revision) select j,id,email,res#>>'{telemetry,wav_sha256}',res->>'model',res->>'model_revision' from safar_users where id=t.owner on conflict do nothing;end if;
end $$;
create function public.safar_attach_ref(u uuid,r uuid) returns void language sql security definer set search_path=public,pg_temp as $$
 update safar_users set referrer=r where id=u and referrer is null and first_paid_at is null and u<>r and exists(select 1 from safar_users where id=r) $$;
create function public.safar_payment(ev text, receipt_id text, provider_in text,u uuid, product text, cents integer,target uuid,sub_id text,ends timestamptz) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare c safar_catalog; x safar_users;
begin
 insert into safar_events values(ev,now()) on conflict do nothing;
 if not found then return;end if;
 select * into strict c from safar_catalog where sku=product;
 if cents<=0 or product='free' or (c.recurring and (sub_id is null or ends is null)) then raise exception 'INVALID_SETTLEMENT';end if;
 if product in ('stems','sync') and not exists(select 1 from safar_tracks where id=target and owner=u and state='committed') then raise exception 'FOREIGN_TRACK';end if;
 select * into strict x from safar_users where id=u for update;
 insert into safar_payments(receipt,provider,owner,sku,subtotal_cents,track_id,subscription_id,period_end) values(receipt_id,provider_in,u,product,cents,target,sub_id,ends) on conflict do nothing;
 if not found then return;end if;
 if c.recurring then
 insert into safar_subscriptions(provider,remote_id,owner,sku,valid_until) values(provider_in,sub_id,u,product,ends)
 on conflict(provider,remote_id) do update set sku=excluded.sku,valid_until=excluded.valid_until where safar_subscriptions.owner=excluded.owner and excluded.valid_until>=safar_subscriptions.valid_until;
 end if;
 perform safar_credit(u,c.credits,'payment',receipt_id);
 if product in ('sync','stems','lora') then insert into safar_grants(owner,sku,track_id,receipt) values(u,product,target,receipt_id);end if;
 if x.first_paid_at is null then
 update safar_users set first_paid_at=now() where id=u;
 if x.referrer is not null then
 insert into safar_referrals values(u,x.referrer,receipt_id,false);
 perform safar_credit(u,100,'referral','refer-in:'||u);perform safar_credit(x.referrer,100,'referral','refer-out:'||u);
 end if;end if;
end $$;
create function public.safar_reverse(ev text,receipt_id text,total_refunded integer) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare p safar_payments;c safar_catalog; r safar_referrals; n integer;credit_delta integer;
begin
 select * into strict p from safar_payments where receipt=receipt_id for update;
 insert into safar_events values(ev,now()) on conflict do nothing;if not found then return;end if;
 n:=least(p.subtotal_cents,total_refunded);if n<=p.refunded_cents then return;end if;
 select * into strict c from safar_catalog where sku=p.sku;
 credit_delta:=floor(c.credits*p.refunded_cents::numeric/p.subtotal_cents)-floor(c.credits*n::numeric/p.subtotal_cents);
 perform safar_credit(p.owner,credit_delta,'payment_refund','reverse:'||p.receipt||':'||n);
 update safar_payments set refunded_cents=n where receipt=p.receipt;
 if n=p.subtotal_cents then
 update safar_grants set revoked=true where receipt=p.receipt;
 if p.subscription_id is not null then
 update safar_subscriptions set valid_until=coalesce((select max(period_end) from safar_payments where provider=p.provider and subscription_id=p.subscription_id and refunded_cents<subtotal_cents),now()) where provider=p.provider and remote_id=p.subscription_id;end if;
 select * into r from safar_referrals where receipt=p.receipt and not reversed;
 if found then perform safar_credit(p.owner,-100,'referral_refund','rr-in:'||p.receipt);perform safar_credit(r.referrer,-100,'referral_refund','rr-out:'||p.receipt);update safar_referrals set reversed=true where receipt=p.receipt;end if;
 end if;
end $$;
create function public.safar_add_seat(u uuid,who text) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare m uuid;
begin perform 1 from safar_users where id=u for update;
 if safar_plan(u)<>'label' then raise exception 'LABEL_REQUIRED';end if;
 select id into strict m from safar_users where lower(email)=lower(who);
 if exists(select 1 from safar_seats where owner=u and member=m) then return;end if;
 if (select count(*) from safar_seats where owner=u)>=4 then raise exception 'TEAM_FULL';end if;
 insert into safar_seats values(m,u);
end $$;
create function public.safar_economics() returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
with periods as(select date_trunc('month',now() at time zone 'UTC') at time zone 'UTC' as m),
tiers as(select sku,price_cents from safar_catalog where recurring),
users_by as(select id,safar_plan(id) as plan from safar_users),
report as(select sku,price_cents*(select count(*) from users_by where plan=sku and exists(select 1 from safar_subscriptions s where s.owner=users_by.id and s.valid_until>now() and s.renewing))/100.0 as mrr_usd,
 coalesce((select sum(subtotal_cents-refunded_cents)/100.0 from safar_payments pay where pay.created_at>=m and (case when pay.sku in ('creator','pro','label') then pay.sku else safar_plan(pay.owner) end)=tiers.sku),0) as revenue,
 coalesce((select sum(t.gpu_usd) from safar_tracks t where t.created_at>=m and t.plan=tiers.sku),0) as gpu_estimate,
 costs.gpu_usd as actual_gpu,costs.other_usd,costs.fee_usd from tiers cross join periods left join safar_costs costs on costs.month=m::date and costs.plan=tiers.sku)
select jsonb_build_object('tiers',jsonb_agg(to_jsonb(report)||jsonb_build_object('net_margin_pct',case when revenue>0 and actual_gpu is not null then round((revenue-actual_gpu-fee_usd-other_usd)/revenue*100,2) end)),
'free_to_paid_pct',(select round(100.0*count(first_paid_at)/nullif(count(*),0),2) from safar_users),'period',date_trunc('month',now())) from report $$;
do $$ declare obj record;begin
 for obj in select tablename from pg_tables where schemaname='public' and left(tablename,6)='safar_' loop
 execute format('alter table public.%I enable row level security',obj.tablename);
 execute format('revoke all on public.%I from public,anon,authenticated',obj.tablename);
 execute format('grant all on public.%I to service_role',obj.tablename);
 end loop;
 for obj in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and left(p.proname,6)='safar_' loop
 execute format('revoke all on function %s from public,anon,authenticated',obj.sig);
 execute format('grant execute on function %s to service_role',obj.sig);
 end loop;
end $$;
grant usage,select on sequence public.safar_ledger_id_seq to service_role;
commit;
