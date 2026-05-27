#!/usr/bin/env python3
"""LG SafeLink AI Pro — Gemini REST + Vision (MISO AIR + Photo Assess)"""
import os, json, re, datetime, base64
import requests as req
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv



load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

API_KEY = os.getenv('GEMINI_API_KEY', '')
MODEL = 'gemini-flash-latest'
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'
JSA_DB_FILE = 'jsa_db.json'
AI_READY = bool(API_KEY)

if AI_READY:
    print(f"✅ API Key → {MODEL}")
else:
    print("⚠️ GEMINI_API_KEY 미설정")


SYSTEM = """당신은 LG전자 창원사업장 안전보건 전문 AI '세피(SAPI)'입니다.

[답변 스타일 규칙]
1. 마크다운 문법 절대 금지 (**, ##, ``` 사용 금지)
2. 이모지로 구분 (📌 ⚠️ ✅ 🔹 💡 🛡️ 🔴 🟡 🟢)
3. 짧은 문장 (2줄 이내)
4. 불릿은 이모지 또는 숫자 (1. 2. 3.)
5. 결론 → 핵심 → 유의사항 순서
6. 한국어만, 구체적이고 실무적
7. 위험등급: 🔴 상 / 🟡 중 / 🟢 하"""


def ask(prompt, json_mode=False):
    if not API_KEY: return None
    try:
        body = {
            'contents': [{'role':'user','parts':[{'text': SYSTEM+'\n\n'+prompt}]}]
        }
        if json_mode:
            body['generationConfig'] = {'responseMimeType':'application/json'}
        r = req.post(f'{GEMINI_URL}?key={API_KEY}', json=body,
                     headers={'Content-Type':'application/json'}, timeout=30)
        if r.status_code == 200:
            return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        print(f"Gemini {r.status_code}: {r.json().get('error',{}).get('message','')[:100]}")
        return None
    except Exception as e:
        print(f"Gemini: {e}")
        return None

def ask_vision(prompt, image_b64):
    """Gemini Vision API — 이미지+텍스트"""
    if not API_KEY: return None
    try:
        # Remove data:image/...;base64, prefix
        if ',' in image_b64:
            mime = image_b64.split(';')[0].split(':')[1] if ':' in image_b64 else 'image/jpeg'
            image_b64 = image_b64.split(',')[1]
        else:
            mime = 'image/jpeg'

        body = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': SYSTEM + '\n\n' + prompt},
                    {'inline_data': {'mime_type': mime, 'data': image_b64}}
                ]
            }]
        }
        r = req.post(f'{GEMINI_URL}?key={API_KEY}', json=body,
                     headers={'Content-Type':'application/json'}, timeout=60)
        if r.status_code == 200:
            return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        print(f"Vision {r.status_code}: {r.json().get('error',{}).get('message','')[:100]}")
        return None
    except Exception as e:
        print(f"Vision: {e}")
        return None

def pj(text):
    if not text: return None
    text = re.sub(r'```json\s*','',re.sub(r'```\s*','',text)).strip()
    try: return json.loads(text)
    except:
        for p in [r'\[.*\]',r'\{.*\}']:
            m = re.search(p, text, re.DOTALL)
            if m:
                try: return json.loads(m.group())
                except: pass
    return None

def load_db():
    try:
        with open(JSA_DB_FILE,'r',encoding='utf-8') as f: return json.load(f)
    except: return []

def save_db(db):
    with open(JSA_DB_FILE,'w',encoding='utf-8') as f: json.dump(db,f,ensure_ascii=False,indent=2)

def search_db(db, wt='', wn='', top_k=3):
    results = []
    words = [w for w in wn.split() if len(w)>=2] if wn else []
    for jsa in db:
        score = 0
        if wt and jsa.get('type')==wt: score += 50
        for w in words:
            if w in jsa.get('name',''): score += 20
        results.append((score, jsa))
    results.sort(key=lambda x:-x[0])
    return [r[1] for r in results[:top_k] if r[0]>0]

@app.route('/api/health')
def health():
    return jsonify({'status':'ok','ai_enabled':AI_READY,'model':MODEL if AI_READY else None,
                    'features':['autogen','similar','accident','recommend','chat','jsa-chat','photo-assess'],
                    'jsa_db_count':len(load_db())})

# ── 1. AI 자동생성 (MISO AIR 스타일) ──
@app.route('/api/autogen', methods=['POST'])
def api_autogen():
    d = request.json or {}
    wt,wn,zone = d.get('work_type',''),d.get('work_name',''),d.get('zone','')
    if not wt: return jsonify({'error':'작업유형 선택'}),400
    db = load_db()
    similar = search_db(db, wt, wn, 2)
    ref = ''
    if similar:
        ref = '\n참고 JSA:\n'
        for s in similar: ref += f"- {s.get('name','')}\n"
    prompt = f"""[MISO AIR 스타일] 작업 JSA를 JSON 배열로 생성하세요.
작업유형: {wt}
작업명: {wn or wt}
구역: {zone or '미지정'}

KRAS 기법(빈도×강도) 기반 4~6단계.
각 단계: step(작업단계), h(위험요인), lv(상/중/하), cause(상세 원인), ctrl(구체적 안전대책 콤마구분)
SIF(심각한 부상/사망) 가능성도 고려하여 위험등급 산정.
JSON 배열만 출력.{ref}"""
    data = pj(ask(prompt, True))
    if data and isinstance(data,list) and len(data)>0:
        return jsonify({'steps':data,'source':'gemini','db_ref':len(similar)})
    return jsonify({'fallback':True}),200

# ── 2. 유사 JSA ──
@app.route('/api/similar', methods=['POST'])
def api_similar():
    d = request.json or {}
    wt,wn = d.get('work_type',''),d.get('work_name','')
    db = load_db()
    db_results = search_db(db, wt, wn, 3)
    if db_results:
        items = [{'id':j.get('id',f'DB-{i+1}'),'title':j.get('name',''),'type':j.get('type',''),'zone':j.get('zone',''),'date':j.get('date',''),'author':j.get('author',''),'steps':len(j.get('steps',[])),'match':max(95-i*12,60),'tags':[]} for i,j in enumerate(db_results)]
        return jsonify({'results':items,'source':'jsa_db'})
    prompt = f"""유사 JSA 3건 JSON. 작업유형:{wt} 작업명:{wn or wt}
id,title,type,zone,date,author,steps,match(65~95),tags. JSON만."""
    data = pj(ask(prompt, True))
    if data and isinstance(data,list):
        return jsonify({'results':data[:3],'source':'gemini'})
    return jsonify({'fallback':True}),200

# ── 3. 사고사례 ──
@app.route('/api/accident', methods=['POST'])
def api_accident():
    wt = (request.json or {}).get('work_type','')
    raw = ask(f"'{wt}' 산업재해 사고사례 3건.\n📌 [사고] → 원인\n💡 교훈\n⚠️ 주의사항. 이모지. 텍스트만.")
    return jsonify({'text':raw,'source':'gemini'}) if raw else (jsonify({'fallback':True}),200)

# ── 4. AI 추천 ──
@app.route('/api/recommend', methods=['POST'])
def api_recommend():
    d = request.json or {}
    raw = ask(f"'{d.get('work_type','')}' JSA 검토:\n{d.get('current_steps','[]')}\n누락 안전대책 5~7개. 🔹 형식. 텍스트만.")
    return jsonify({'text':raw,'source':'gemini'}) if raw else (jsonify({'fallback':True}),200)

# ── 5. 챗봇 ──
@app.route('/api/chat', methods=['POST'])
def api_chat():
    msg = (request.json or {}).get('message','')
    if not msg.strip(): return jsonify({'error':'메시지 필요'}),400
    raw = ask(f"사용자: {msg}\n\n세피로서 답변. 친절+전문. 불릿 간결. 이모지.")
    return jsonify({'reply':raw,'source':'gemini'}) if raw else (jsonify({'fallback':True}),200)

# ── 6. 대화형 JSA ──
@app.route('/api/jsa-chat', methods=['POST'])
def api_jsa_chat():
    d = request.json or {}
    msg,history = d.get('message',''),d.get('history',[])
    wt,wn = d.get('work_type',''),d.get('work_name','')
    conv = '\n'.join([f"{'세피' if h.get('role')=='assistant' else '사용자'}: {h.get('content','')}" for h in history[-6:]])
    prompt = f"""대화형 JSA AI. 사용자와 대화하며 JSA를 완성합니다.
작업유형: {wt or '미정'}, 작업명: {wn or '미정'}

이전 대화:
{conv}

사용자: {msg}

규칙:
1. 부족한 정보 → 질문 (높이, 장비, 인원, 환경)
2. 정보 충분 → JSA 생성 안내
3. JSA 준비 → 응답 끝에 [JSA_READY] + JSON 배열
4. JSON: [{{"step":"단계","h":"위험","lv":"상/중/하","cause":"원인","ctrl":"대책"}}]
5. 이모지 사용. 친절."""
    raw = ask(prompt)
    if not raw:
        return jsonify({'reply':'AI가 현재 응답할 수 없습니다.\n\n💡 상단 AI 자동생성을 이용해 보세요!','jsa_ready':False})
    jsa_ready = '[JSA_READY]' in raw
    steps = None; reply = raw
    if jsa_ready:
        parts = raw.split('[JSA_READY]')
        reply = parts[0].strip()
        if len(parts)>1: steps = pj(parts[1])
    return jsonify({'reply':reply,'jsa_ready':jsa_ready,'steps':steps,'source':'gemini'})

# ── 7. 📷 사진 위험성평가 (ai-riska 스타일) ──
@app.route('/api/photo-assess', methods=['POST'])
def api_photo_assess():
    d = request.json or {}
    image = d.get('image','')
    wt = d.get('work_type','')
    wn = d.get('work_name','')

    if not image:
        return jsonify({'error':'이미지 필요'}), 400

    prompt = f"""이 현장 사진을 분석하여 위험성평가를 수행하세요.

{'작업유형: '+wt if wt else ''}
{'작업명: '+wn if wn else ''}

다음 형식으로 답변하세요:

📍 현장 상황 분석
- 사진에서 보이는 작업 환경/상황 설명

⚠️ 식별된 위험요인
1. [위험요인] — 위험등급(상/중/하) — [원인]
2. ...

🛡️ 필요 안전대책
1. [대책 상세]
2. ...

🦺 필요 보호구
- [목록]

📋 JSA 단계 (자동생성)
이 작업의 JSA 단계를 4~6개로 자동 생성하세요.

마지막에 [JSA_DATA] 태그 뒤에 JSON 배열을 추가하세요:
[JSA_DATA][{{"step":"단계","h":"위험","lv":"상/중/하","cause":"원인","ctrl":"대책"}}]"""

    raw = ask_vision(prompt, image)
    if not raw:
        return jsonify({'error':'AI 분석 실패'}), 200

    steps = None
    text = raw
    if '[JSA_DATA]' in raw:
        parts = raw.split('[JSA_DATA]')
        text = parts[0].strip()
        if len(parts) > 1:
            steps = pj(parts[1])

    return jsonify({'text': text, 'steps': steps, 'source': 'gemini-vision'})

# ── JSA DB ──
@app.route('/api/jsa-db/save', methods=['POST'])
def api_jsa_save():
    d = request.json or {}
    db = load_db()
    entry = {'id':f"JSA-{len(db)+1:04d}",'type':d.get('type',''),'name':d.get('name',''),'zone':d.get('zone',''),'steps':d.get('steps',[]),'date':datetime.datetime.now().strftime('%Y-%m-%d'),'author':d.get('author','SafeLink')}
    db.append(entry); save_db(db)
    return jsonify({'ok':True,'id':entry['id'],'total':len(db)})

@app.route('/api/jsa-db/search', methods=['POST'])
def api_jsa_search():
    d = request.json or {}
    return jsonify({'results':search_db(load_db(),d.get('work_type',''),d.get('work_name',''))})

@app.route('/api/jsa-db/list')
def api_jsa_list():
    return jsonify({'items':load_db(),'total':len(load_db())})

# ── 날씨 (기상청 단기실황) ──
@app.route('/api/weather')
def api_weather():
    now = datetime.datetime.now()
    if now.minute < 40:
        now = now - datetime.timedelta(hours=1)
    base_date = now.strftime('%Y%m%d')
    base_time = now.strftime('%H00')
    try:
        WEATHER_KEY = 'f71be9f1499b5bcbc403a5e72236288efa21234d9f3b828afb6328b9c148c023'
        url = 'http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst'
        params = {'serviceKey':WEATHER_KEY,'numOfRows':'10','pageNo':'1','dataType':'JSON',
                  'base_date':base_date,'base_time':base_time,'nx':'89','ny':'77'}
        r = req.get(url, params=params, timeout=10)
        data = r.json()
        items = data['response']['body']['items']['item']
        result = {}
        for item in items:
            cat, val = item['category'], item['obsrValue']
            if cat=='T1H': result['temp']=float(val)
            elif cat=='REH': result['humidity']=float(val)
            elif cat=='RN1': result['rain']=float(val)
            elif cat=='WSD': result['wind']=float(val)
            elif cat=='PTY': result['pty']=int(val)
        temp=result.get('temp',0); humidity=result.get('humidity',0); pty=result.get('pty',0)
        feel=round(temp+0.33*(humidity*6.105*(17.27*temp/(237.7+temp)))/100-4.0,1) if temp>=27 else temp
        result['feel_temp']=feel
        wm={0:'맑음',1:'비',2:'비/눈',3:'눈',5:'빗방울',6:'빗방울/눈',7:'눈날림'}
        im={0:'☀️',1:'🌧️',2:'🌨️',3:'❄️',5:'🌦️',6:'🌨️',7:'❄️'}
        result['desc']=wm.get(pty,'맑음'); result['icon']=im.get(pty,'☀️')
        if feel>=35:
            result.update(heat_level='danger',heat_label='🔴 위험',heat_msg=f'체감온도 {feel}°C · 30분 작업 / 30분 휴식',work_min=30,rest_min=30)
        elif feel>=33:
            result.update(heat_level='warning',heat_label='🟠 경고',heat_msg=f'체감온도 {feel}°C · 40분 작업 / 15분 휴식',work_min=40,rest_min=15)
        elif feel>=31:
            result.update(heat_level='caution',heat_label='🟡 주의',heat_msg=f'체감온도 {feel}°C · 50분 작업 / 10분 휴식',work_min=50,rest_min=10)
        else:
            result.update(heat_level='safe',heat_label='🟢 안전',heat_msg=f'체감온도 {feel}°C · 정상 작업 가능',work_min=60,rest_min=0)
        result['location']='창원 성산구'
        return jsonify(result)
    except Exception as e:
        print(f"날씨: {e}")
        return jsonify({'temp':0,'desc':'조회실패','icon':'⚠️','heat_label':'⚠️ 조회실패','heat_msg':'날씨 정보를 가져올 수 없습니다'})

@app.route('/manifest.json')
def pwa_manifest():
    return send_file('manifest.json')

@app.route('/sw.js')
def pwa_sw():
    return send_file('sw.js', mimetype='application/javascript')

@app.route('/icon-192.png')
def pwa_icon_192():
    return send_file('icon-192.png')

@app.route('/icon-512.png')
def pwa_icon_512():
    return send_file('icon-512.png')
# ── 회원가입 ──
@app.route('/api/register', methods=['POST'])
def api_register():
    d = request.json or {}
    name = d.get('name','').strip()
    company = d.get('company','').strip()
    role = d.get('role','').strip()
    phone = d.get('phone','').strip()
    if not name or not company:
        return jsonify({'ok':False,'msg':'이름과 소속을 입력해주세요'})
    user = {'name':name,'company':company,'role':role,'phone':phone,'date':datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
    users = []
    if os.path.exists('users.json'):
        with open('users.json','r',encoding='utf-8') as f:
            users = json.load(f)
    for u in users:
        if u.get('name')==name and u.get('phone')==phone:
            return jsonify({'ok':True,'msg':'이미 등록됨','user':u})
    users.append(user)
    with open('users.json','w',encoding='utf-8') as f:
        json.dump(users,f,ensure_ascii=False,indent=2)
    return jsonify({'ok':True,'msg':'등록 완료!','user':user})

@app.route('/api/users')
def api_users():
    if os.path.exists('users.json'):
        with open('users.json','r',encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])

# ── 공지사항 ──
@app.route('/api/notices')
def api_notices():
    if os.path.exists('notices.json'):
        with open('notices.json','r',encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route('/api/notices/add', methods=['POST'])
def api_notice_add():
    d = request.json or {}
    title = d.get('title','').strip()
    body = d.get('body','').strip()
    author = d.get('author','관리자')
    if not title:
        return jsonify({'ok':False})
    notices = []
    if os.path.exists('notices.json'):
        with open('notices.json','r',encoding='utf-8') as f:
            notices = json.load(f)
    notices.insert(0,{'id':len(notices)+1,'title':title,'body':body,'author':author,'date':datetime.datetime.now().strftime('%Y-%m-%d')})
    with open('notices.json','w',encoding='utf-8') as f:
        json.dump(notices,f,ensure_ascii=False,indent=2)
    return jsonify({'ok':True})

@app.route('/')
def index():
    return send_file('LG_SafeLink_v4_final.html')

if __name__=='__main__':
    port = int(os.getenv('PORT',8080))
    print(f"\n🚀 SafeLink AI Pro: http://localhost:{port}")
    print(f"   AI: {'🟢 Gemini REST+Vision' if AI_READY else '🔴 오프라인'}")
    print(f"   📷 사진 위험성평가: {'✅' if AI_READY else '❌'}")
    print(f"   JSA DB: {len(load_db())}건")
    print(f"   종료: Ctrl+C\n")
    app.run(host='0.0.0.0',port=port,debug=True)
