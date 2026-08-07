#!/usr/bin/env python3
import csv, json, os, re, sys, time, shutil, subprocess, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path('/opt/data/automations/linkedin-sales-signals')
RUNS = BASE / 'runs'
PHRASES_PATH = BASE / 'phrases.json'
STATS_PATH = BASE / 'phrase_stats.json'
EMAIL_SCRIPT = BASE / 'send_csv_email.py'
TO_EMAIL = 'notifications@example.invalid'
WARSAW = ZoneInfo('Europe/Warsaw')
APIFY_BASE = 'https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}'

CSV_HEADERS = [
    'URL oryginalnego posta','Treść oryginalnego posta','Autor posta','Sygnał sprzedażowy',
    'URL profilu LinkedIn','Powód kwalifikacji','Rodzaj interakcji','Treść komentarza'
]

DIRECT_NEED = re.compile(r'\b(szukam|szukamy|potrzebuj[ęe]my|potrzebuj[ęe]|poleci(?:cie|sz|łby|łaby)|rekomenduj(?:ecie)?|kogo polecacie|wdrożenie|wdrożyć|problem z|nie działa|brak|mało|jak pozyskiwać|proszę o kontakt|poproszę o kontakt|kontakt do|baza kontakt|system crm|crm)\b', re.I)
TOPIC = re.compile(r'(sprzedaż|handlow|prospecting|lead|crm|cold mail|cold call|klient|baza kontakt|follow.?up|outbound|automatyzacj|agent AI)', re.I)
POLISH_HINT = re.compile(r'(\bPolska\b|\bPoland\b|\bWarszaw|\bKrak[oó]w|\bWrocław|\bPoznań|\bGdańsk|\bŁódź|\bKatowic|\bLublin|\bSzczecin|\bBydgoszcz|\bBiałystok|\bRzesz[oó]w|\bGliwic|\bPL\b|polsk)', re.I)
ROLE_HINT = re.compile(r'(właściciel|owner|founder|założyciel|prezes|ceo|zarząd|dyrektor|director|manager|kierownik|head of|szef|sales|sprzedaż|handlow|business development|marketing|operations|operacje|customer|obsług)', re.I)
TECH_ROLE = re.compile(r'(developer|programista|automation specialist|it engineer|administrator|architect|devops|software engineer|informatyk)', re.I)
PL_STOP = {'i','oraz','że','nie','dla','jest','się','jak','czy','do','na','po','od','przez','które','który','w','z','to','ale','więc','też','już','jego','jej','ich','pod','nad','za','u','lub'}

def load_dotenv(path='/opt/data/.env'):
    p=Path(path)
    if not p.exists(): return
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        s=line.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1); k=k.strip(); v=v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k]=v

def now_range():
    now = datetime.now(WARSAW)
    current_mon = (now - timedelta(days=now.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)
    if now < current_mon:
        current_mon -= timedelta(days=7)
    prev_mon = current_mon - timedelta(days=7)
    if current_mon - prev_mon != timedelta(days=7):
        raise RuntimeError('Niepoprawny zakres dat: różnica != 7 dni')
    return prev_mon, current_mon

def iso_z(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def norm_url(u):
    if not u: return ''
    u = str(u).strip()
    p = urllib.parse.urlsplit(u)
    q = [(k,v) for k,v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not (k.lower().startswith('utm_') or k.lower() in {'trk','trackingid','miniProfileUrn','lipi'})]
    path = p.path.rstrip('/')
    return urllib.parse.urlunsplit((p.scheme or 'https', p.netloc.lower(), path, urllib.parse.urlencode(q), ''))

def post_key(p):
    return str(p.get('id') or p.get('entityId') or p.get('shareUrn') or norm_url(p.get('linkedinUrl') or p.get('url')))

def parse_dt(obj):
    if isinstance(obj, dict):
        if obj.get('date'):
            s=obj['date'].replace('Z','+00:00')
            try: return datetime.fromisoformat(s).astimezone(timezone.utc)
            except Exception: pass
        if obj.get('timestamp'):
            try: return datetime.fromtimestamp(int(obj['timestamp'])/1000, tz=timezone.utc)
            except Exception: pass
    if isinstance(obj, str):
        try: return datetime.fromisoformat(obj.replace('Z','+00:00')).astimezone(timezone.utc)
        except Exception: return None
    return None

def is_polish_text(txt):
    if not txt or len(txt.strip()) < 20: return False
    low = re.findall(r'[a-ząćęłńóśźż]+', txt.lower())
    if not low: return False
    stop_hits = sum(1 for w in low if w in PL_STOP)
    dia = len(re.findall(r'[ąćęłńóśźż]', txt.lower()))
    return dia >= 1 or stop_hits >= 3

def actor_call(actor_slug, payload, attempts_rec, max_attempts=7, timeout=300):
    token = os.environ.get('APIFY_TOKEN') or os.environ.get('APIFY_API_TOKEN')
    url = APIFY_BASE.format(actor=actor_slug, token=urllib.parse.quote(token))
    last = None
    for attempt in range(1, max_attempts+1):
        attempts_rec.append({'actor': actor_slug, 'attempt': attempt})
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode('utf-8','replace')
            return json.loads(raw) if raw else []
        except urllib.error.HTTPError as e:
            msg = f'HTTP {e.code}: {e.read().decode("utf-8","replace")[:800]}'
            last = msg
            if e.code in (400,422):
                # one schema fallback for post search
                if 'postedLimitDate' in payload:
                    payload = dict(payload); payload.pop('postedLimitDate', None); payload.setdefault('postedLimit','week')
                elif 'posts' in payload:
                    pass
            time.sleep(min(2*attempt, 15))
        except Exception as e:
            last = f'{type(e).__name__}: {str(e)[:800]}'
            time.sleep(min(2*attempt, 15))
    raise RuntimeError(last or 'unknown actor error')

def person_fields(actor):
    if not isinstance(actor, dict): actor={}
    name = actor.get('name') or actor.get('fullName') or actor.get('title') or ''
    url = norm_url(actor.get('linkedinUrl') or actor.get('url') or actor.get('profileUrl') or '')
    pos = actor.get('position') or actor.get('headline') or actor.get('info') or actor.get('jobTitle') or ''
    company = actor.get('companyName') or actor.get('company') or ''
    loc = actor.get('location') or actor.get('geoLocationName') or actor.get('address') or ''
    public = actor.get('publicIdentifier') or ''
    return name, url, pos, company, loc, public

def poland_confirmed(*vals):
    text=' '.join(str(v or '') for v in vals)
    return bool(POLISH_HINT.search(text) or re.search(r'[ąćęłńóśźż]', text.lower()))

def role_ok(pos, company, interaction_text, direct=False):
    t=' '.join([pos or '', company or ''])
    if direct or DIRECT_NEED.search(interaction_text or ''): return True
    if TECH_ROLE.search(t): return False
    return bool(ROLE_HINT.search(t))

def run_cleanup(run_log):
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    removed=[]
    RUNS.mkdir(exist_ok=True)
    for child in RUNS.iterdir():
        if child.is_dir():
            try:
                mt=datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
                if mt < cutoff:
                    shutil.rmtree(child); removed.append(str(child))
            except Exception as e:
                run_log['errors'].append(f'cleanup {child}: {e}')
    run_log['cleanup']={'removed_runs': removed}

def write_stats(phrases, phrase_counts, csv_counts):
    old={}
    if STATS_PATH.exists():
        try: old=json.loads(STATS_PATH.read_text(encoding='utf-8'))
        except Exception: old={}
    history=old.get('history', []) if isinstance(old,dict) else []
    stamp=datetime.now(WARSAW).date().isoformat()
    week={p:{'posts': phrase_counts.get(p,0), 'records_csv': csv_counts.get(p,0)} for p in phrases}
    history.append({'week': stamp, 'phrases': week})
    history=history[-4:]
    summary={}
    for p in phrases:
        last3=[h.get('phrases',{}).get(p,{}).get('records_csv',0) for h in history[-3:]]
        summary[p]={'last_posts': phrase_counts.get(p,0),'last_records_csv': csv_counts.get(p,0),'zero_csv_streak': (len(last3) if len(last3)==3 and all(x==0 for x in last3) else (sum(1 for x in reversed(last3) if x==0)))}
    STATS_PATH.write_text(json.dumps({'updated_at': datetime.now(timezone.utc).isoformat(), 'history': history, 'summary': summary}, ensure_ascii=False, indent=2), encoding='utf-8')

def send_email(csv_path, subject_date, body):
    cmd=[sys.executable, str(EMAIL_SCRIPT), '--to', TO_EMAIL, '--subject', f'Cotygodniowe sygnały sprzedażowe z LinkedIn — {subject_date}', '--body', body, '--attachment', str(csv_path)]
    p=subprocess.run(cmd, text=True, capture_output=True, timeout=180)
    out=(p.stdout or p.stderr or '').strip()
    try: parsed=json.loads(out)
    except Exception: parsed={'ok': p.returncode==0, 'raw': out[:1000]}
    if p.returncode!=0 and 'ok' not in parsed: parsed['ok']=False
    return parsed

def main():
    load_dotenv()
    start_w, end_w = now_range(); start_z=iso_z(start_w); end_z=iso_z(end_w)
    run_id=os.environ.get('LINKEDIN_RUN_ID') or datetime.now(WARSAW).strftime('%Y%m%d_%H%M%S')
    run_dir=RUNS/run_id; run_dir.mkdir(parents=True, exist_ok=True)
    run_log={'run_id':run_id,'start':datetime.now(timezone.utc).isoformat(),'range':{'start_warsaw':start_w.isoformat(),'end_warsaw':end_w.isoformat(),'start_utc':start_z,'end_utc':end_z},'phrases':[], 'posts_per_phrase':{}, 'unique_posts':0, 'comments':0,'reactions':0,'qualified':0,'rejected':0,'manual_review':0,'actor_status':{},'errors':[],'attempts':[],'partial':False,'csv':None,'email_status':None,'phrase_changes':[],'cleanup':{}}
    token=os.environ.get('APIFY_TOKEN') or os.environ.get('APIFY_API_TOKEN')
    phrases=[]
    try:
        data=json.loads(PHRASES_PATH.read_text(encoding='utf-8'))
        raw=data.get('active_phrases') if isinstance(data,dict) else data
        seen=set()
        for x in raw or []:
            p=str(x).strip()
            if p and len(p)<=85 and p.lower() not in seen and is_polish_text(p+' sprzedaż klient automatyzacja'):
                phrases.append(p); seen.add(p.lower())
    except Exception as e:
        run_log['errors'].append(f'phrases read/validate: {e}')
    run_log['phrases']=phrases
    if not token:
        csv_path=run_dir/'BŁĄD - brak tokenu Apify.csv'
        with csv_path.open('w',encoding='utf-8',newline='') as f: csv.writer(f).writerows([CSV_HEADERS])
        msg='Brak APIFY_TOKEN/APIFY_API_TOKEN po załadowaniu /opt/data/.env; pobieranie danych nie zostało uruchomione.'
        email=send_email(csv_path, end_w.date().isoformat(), msg)
        run_log.update({'csv':str(csv_path),'email_status':email,'errors':[msg]})
        (run_dir/'run_log.json').write_text(json.dumps(run_log,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':'error','range':run_log['range'],'csv':str(csv_path),'records':0,'email':email,'partial_errors':[msg],'cleanup':{}}, ensure_ascii=False))
        return 0
    if not phrases:
        csv_path=run_dir/'BŁĄD - brak aktywnych fraz.csv'
        with csv_path.open('w',encoding='utf-8',newline='') as f: csv.writer(f).writerows([CSV_HEADERS])
        msg='Brak aktywnych poprawnych fraz; pobieranie danych nie zostało uruchomione.'
        email=send_email(csv_path, end_w.date().isoformat(), msg)
        run_log.update({'csv':str(csv_path),'email_status':email,'errors':[msg]})
        (run_dir/'run_log.json').write_text(json.dumps(run_log,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':'error','range':run_log['range'],'csv':str(csv_path),'records':0,'email':email,'partial_errors':[msg],'cleanup':{}}, ensure_ascii=False))
        return 0

    posts_by_key={}; phrase_counts={p:0 for p in phrases}; raw_dir=run_dir/'raw'; raw_dir.mkdir(exist_ok=True)
    resume_posts_path = raw_dir/'unique_posts.json'
    if os.environ.get('LINKEDIN_RESUME_POSTS') and resume_posts_path.exists():
        posts = json.loads(resume_posts_path.read_text(encoding='utf-8'))
        for post in posts:
            for ph in post.get('_matched_phrases', []):
                if ph in phrase_counts:
                    phrase_counts[ph] += 1
        run_log['posts_per_phrase'] = phrase_counts
        run_log['actor_status']['post_search']='resumed'
    else:
        def fetch_phrase(p):
            attempts=[]
            payload={'searchQueries':[p], 'sortBy':'date', 'postedLimitDate':start_z, 'postedLimit':'week', 'maxPosts':0, 'scrapeComments':False, 'scrapeReactions':False}
            arr=actor_call('harvestapi~linkedin-post-search', payload, attempts, timeout=420)
            return p, arr, attempts
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs={ex.submit(fetch_phrase,p):p for p in phrases}
            for fut in as_completed(futs):
                p=futs[fut]
                try:
                    phrase, arr, attempts=fut.result(); run_log['attempts'].extend(attempts)
                    (raw_dir/(re.sub(r'[^\wąćęłńóśźż.-]+','_',phrase, flags=re.I)[:80]+'.posts.json')).write_text(json.dumps(arr,ensure_ascii=False),encoding='utf-8')
                    filtered=[]
                    for post in arr:
                        dt=parse_dt(post.get('postedAt') or post.get('date') or post.get('createdAt'))
                        if not dt or not (start_w.astimezone(timezone.utc) <= dt < end_w.astimezone(timezone.utc)): continue
                        content=post.get('content') or post.get('text') or ''
                        if not is_polish_text(content): continue
                        filtered.append(post)
                        k=post_key(post)
                        if k not in posts_by_key:
                            post['_matched_phrases']=[phrase]
                            posts_by_key[k]=post
                        else:
                            posts_by_key[k].setdefault('_matched_phrases',[]).append(phrase)
                    phrase_counts[phrase]=len(filtered)
                    run_log['posts_per_phrase'][phrase]=len(filtered)
                    run_log['actor_status'][f'post_search:{phrase}']='ok'
                except Exception as e:
                    run_log['partial']=True; run_log['errors'].append(f'post_search {p}: {e}'); run_log['actor_status'][f'post_search:{p}']='error'
        posts=list(posts_by_key.values())
    run_log['unique_posts']=len(posts)
    topical=[]
    for p in posts:
        txt=p.get('content') or ''
        if TOPIC.search(txt) or any(ph.lower() in txt.lower() for ph in p.get('_matched_phrases',[])):
            topical.append(p)
    (raw_dir/'unique_posts.json').write_text(json.dumps(posts,ensure_ascii=False),encoding='utf-8')

    # comments and reactions in chunks
    comments=[]; reactions=[]
    urls=[norm_url(p.get('linkedinUrl') or p.get('url')) for p in topical if norm_url(p.get('linkedinUrl') or p.get('url'))]
    # Interaction actors can be slow on large post sets. Keep the weekly run bounded:
    # one failed/slow chunk must not block CSV/email delivery for hours.
    interaction_max = int(os.environ.get('LINKEDIN_INTERACTION_MAX_ITEMS') or '25')
    interaction_chunk = int(os.environ.get('LINKEDIN_INTERACTION_CHUNK_SIZE') or '5')
    interaction_timeout = int(os.environ.get('LINKEDIN_INTERACTION_TIMEOUT') or '90')
    interaction_attempts = int(os.environ.get('LINKEDIN_INTERACTION_ATTEMPTS') or '1')
    interaction_budget = int(os.environ.get('LINKEDIN_INTERACTION_TOTAL_BUDGET') or '300')
    interaction_deadline = time.monotonic() + interaction_budget
    if interaction_max:
        run_log['partial'] = True
        run_log['errors'].append(f'interakcje pobrane z limitem maxItems={interaction_max}; pełne komentarze/reakcje mogą być niepełne')
    for i in range(0,len(urls),interaction_chunk):
        if time.monotonic() >= interaction_deadline:
            run_log['partial']=True
            run_log['errors'].append(f'przerwano pobieranie komentarzy/reakcji po budżecie {interaction_budget}s; pozostałe URL-e od chunk {i//interaction_chunk} pominięte')
            run_log['actor_status'][f'interactions:{i//interaction_chunk}']='budget_exceeded'
            break
        chunk=urls[i:i+interaction_chunk]
        try:
            arr=actor_call('harvestapi~linkedin-post-comments', {'posts':chunk,'maxItems':interaction_max,'postedLimit':'week','scrapeReplies':True,'profileScraperMode':'main'}, run_log['attempts'], max_attempts=interaction_attempts, timeout=interaction_timeout)
            comments.extend(arr); run_log['actor_status'][f'comments:{i//interaction_chunk}']='ok'
        except Exception as e:
            run_log['partial']=True; run_log['errors'].append(f'comments chunk {i//interaction_chunk}: {e}'); run_log['actor_status'][f'comments:{i//interaction_chunk}']='error'
        if time.monotonic() >= interaction_deadline:
            run_log['partial']=True
            run_log['errors'].append(f'przerwano pobieranie reakcji po budżecie {interaction_budget}s; pozostałe URL-e od chunk {i//interaction_chunk} pominięte')
            run_log['actor_status'][f'reactions:{i//interaction_chunk}']='budget_exceeded'
            break
        try:
            arr=actor_call('harvestapi~linkedin-post-reactions', {'posts':chunk,'maxItems':interaction_max,'profileScraperMode':'main'}, run_log['attempts'], max_attempts=interaction_attempts, timeout=interaction_timeout)
            reactions.extend(arr); run_log['actor_status'][f'reactions:{i//interaction_chunk}']='ok'
        except Exception as e:
            run_log['partial']=True; run_log['errors'].append(f'reactions chunk {i//interaction_chunk}: {e}'); run_log['actor_status'][f'reactions:{i//interaction_chunk}']='error'
    run_log['comments']=len(comments); run_log['reactions']=len(reactions)
    (raw_dir/'comments.json').write_text(json.dumps(comments,ensure_ascii=False),encoding='utf-8')
    (raw_dir/'reactions.json').write_text(json.dumps(reactions,ensure_ascii=False),encoding='utf-8')

    post_by_url={norm_url(p.get('linkedinUrl') or p.get('url')):p for p in topical}
    post_by_id={str(p.get('id') or p.get('entityId') or ''):p for p in topical}
    def find_post(item):
        for key in ['postUrl','linkedinUrl','url']:
            u=norm_url(item.get(key))
            if u in post_by_url: return post_by_url[u]
        pid=str(item.get('postId') or item.get('activityId') or '')
        m=re.search(r'activity:(\d+)', pid)
        ids=[pid, m.group(1) if m else '']
        for p in topical:
            if any(x and x in str(p.get('id') or '')+str(p.get('entityId') or '')+str(p.get('shareUrn') or '')+str(p.get('linkedinUrl') or '') for x in ids): return p
        return None

    records=[]; rejected=0; manual=0; phrase_csv_counts={p:0 for p in phrases}
    # author direct needs
    for p in topical:
        content=p.get('content') or ''
        if DIRECT_NEED.search(content):
            author=p.get('author') or {}
            name,url,pos,company,loc,pub=person_fields(author)
            if url and (pos or company) and poland_confirmed(name,pos,company,loc,pub,content):
                rec=[norm_url(p.get('linkedinUrl') or p.get('url')), content, (author.get('name') or ''), f'{name}: autor opisał potrzebę/problem', url, 'Bezpośrednia potrzeba w poście autora', 'post autora', '']
                records.append((rec,p.get('_matched_phrases',[])))
            else: rejected+=1
    merged={}
    for c in comments:
        p=find_post(c)
        if not p: rejected+=1; continue
        actor=c.get('actor') or c.get('author') or c.get('profile') or {}
        name,url,pos,company,loc,pub=person_fields(actor)
        pauthor=p.get('author') or {}; pa_url=norm_url((pauthor or {}).get('linkedinUrl') or '')
        if not url or url==pa_url or (not pos and not company): rejected+=1; continue
        text=c.get('text') or c.get('comment') or c.get('content') or c.get('message') or ''
        if not is_polish_text(text) and not DIRECT_NEED.search(text): rejected+=1; continue
        if not poland_confirmed(name,pos,company,loc,pub,text): rejected+=1; continue
        direct=bool(DIRECT_NEED.search(text))
        if not role_ok(pos, company, text, direct=direct): rejected+=1; continue
        key=(norm_url(p.get('linkedinUrl') or p.get('url')), url)
        ent=merged.setdefault(key, {'post':p,'name':name,'url':url,'pos':pos,'company':company,'comments':[],'reaction':False,'direct':False,'manual':False})
        ent['comments'].append(text); ent['direct'] = ent['direct'] or direct
    for r in reactions:
        p=find_post(r)
        if not p: rejected+=1; continue
        actor=r.get('actor') or r.get('profile') or {}
        name,url,pos,company,loc,pub=person_fields(actor)
        pauthor=p.get('author') or {}; pa_url=norm_url((pauthor or {}).get('linkedinUrl') or '')
        if not url or url==pa_url or (not pos and not company): rejected+=1; continue
        if not poland_confirmed(name,pos,company,loc,pub): rejected+=1; continue
        if not role_ok(pos, company, '', direct=False): rejected+=1; continue
        key=(norm_url(p.get('linkedinUrl') or p.get('url')), url)
        ent=merged.setdefault(key, {'post':p,'name':name,'url':url,'pos':pos,'company':company,'comments':[],'reaction':False,'direct':False,'manual':False})
        ent['reaction']=True
    for ent in merged.values():
        p=ent['post']; content=p.get('content') or ''; pauthor=p.get('author') or {}
        kind='komentarz i reakcja' if ent['comments'] and ent['reaction'] else ('komentarz' if ent['comments'] else 'reakcja')
        if ent['comments']:
            reason='Komentarz wskazuje potrzebę/problem' if ent['direct'] else 'Komentarz/rola powiązane z tematem; do ręcznej oceny'
        else:
            reason='Reakcja na tematyczny post + rola sprzedaż/marketing/zarządzanie/właściciel'
        if 'do ręcznej oceny' in reason: manual+=1
        rec=[norm_url(p.get('linkedinUrl') or p.get('url')), content, (pauthor.get('name') or ''), f"{ent['name']}: {reason}", ent['url'], reason, kind, '\n'.join(ent['comments'])]
        records.append((rec,p.get('_matched_phrases',[])))
    # de-dupe exact profile/post
    seen=set(); rows=[]
    for rec, phs in records:
        k=(rec[0], rec[4], rec[6], rec[7])
        if k in seen: continue
        seen.add(k); rows.append(rec)
        for ph in phs: phrase_csv_counts[ph]=phrase_csv_counts.get(ph,0)+1
    run_log['qualified']=len(rows); run_log['rejected']=rejected; run_log['manual_review']=manual
    csv_name = f'sygnaly_sprzedazowe_{end_w.date().isoformat()}.csv' if rows else 'Nie znaleziono rekordów w tym tygodniu.csv'
    csv_path=run_dir/csv_name
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        w=csv.writer(f); w.writerow(CSV_HEADERS); w.writerows(rows)
    if not csv_path.exists(): raise RuntimeError('brak zapisu CSV')
    run_log['csv']=str(csv_path)
    write_stats(phrases, phrase_counts, phrase_csv_counts)
    email_body=f"Zakres: {start_w.isoformat()} → {end_w.isoformat()}\nLiczba fraz: {len(phrases)}\nLiczba znalezionych postów: {len(posts)}\nLiczba rekordów CSV: {len(rows)}"
    if run_log['partial']:
        email_body += '\nCzęściowy wynik: ' + '; '.join(run_log['errors'][:10])
    try:
        run_log['email_status']=send_email(csv_path, end_w.date().isoformat(), email_body)
    except Exception as e:
        run_log['email_status']={'ok':False,'errors':[str(e)[:1000]]}
    run_cleanup(run_log)
    run_log['end']=datetime.now(timezone.utc).isoformat()
    (run_dir/'run_log.json').write_text(json.dumps(run_log,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'partial' if run_log['partial'] else 'ok','range':run_log['range'],'csv':str(csv_path),'records':len(rows),'email':run_log['email_status'],'partial_errors':run_log['errors'][:20],'cleanup':run_log['cleanup']}, ensure_ascii=False))

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'status':'fatal','error':str(e)[:1000]}, ensure_ascii=False))
        raise
