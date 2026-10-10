/* Process telemetry is observed independently from MQTT command acceptance. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const ids = ['motor', 'conveyor', 'camera'];
  const labels = {motor:'진단 모터', conveyor:'컨베이어', camera:'검사 카메라'};
  const codes = {motor:'M-01', conveyor:'C-01', camera:'V-01'};
  const names = {normal:'결함 미검출', caution:'주의', warning:'경고', danger:'위험', scratch:'스크래치', dent:'찍힘', contamination:'이물질'};
  const sourceNames = {pdm:'모터 진단', controller:'라인 제어', camera:'품질 검사', simulator:'시뮬레이터', system:'시스템'};
  const stateNames = new Set(['normal','caution','warning','danger','running','stopped','waiting','stale','attention','offline']);
  let snapshot = null, receivedAt = 0, online = false, selectedEquipment = 'motor';
  let selectedInspectionId = null, selectedInspection = null, displayedInspection = null;
  let currentImagePath = '', currentCamPath = '', pendingCommand = null, commandBusy = false;
  let sliderTouched = false, staleSignature = '', refreshTimer;
  const num = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const fmt = (value, precision=1) => num(value) === null ? '—' : value.toFixed(precision);
  const integer = value => num(value) === null ? '—' : value.toLocaleString();
  const time = value => { const date = new Date(value); return value && Number.isFinite(date.getTime()) ? date.toLocaleTimeString('ko-KR', {hour12:false}) : '—'; };
  const fullTime = value => { const date = new Date(value); return value && Number.isFinite(date.getTime()) ? date.toLocaleString('ko-KR', {hour12:false}) : '시각 없음'; };
  const text = (id, value) => { $(id).textContent = value; };
  const cap = id => id[0].toUpperCase() + id.slice(1);

  function badge(id, state, label) {
    const element = $(id);
    element.className = 'status-badge state-' + (stateNames.has(state) ? state : 'waiting');
    element.textContent = label;
  }
  function metric(id, value, precision, unit) {
    const small = document.createElement('small');
    small.textContent = unit;
    $(id).replaceChildren(document.createTextNode(fmt(value, precision) + ' '), small);
  }
  function age(eq) {
    return num(eq?.age_seconds) === null ? null : eq.age_seconds + Math.max(0, performance.now()-receivedAt)/1000;
  }
  function rawEquipment(id) {
    return snapshot?.process?.equipment?.find(item => item.id === id) ||
      {id, label:labels[id], state:'waiting', status_label:'데이터 대기', fresh:false, metrics:{}, reason:'관측 데이터 수집 중'};
  }
  function isFresh(eq) {
    const elapsed = age(eq);
    return online && eq.fresh === true && elapsed !== null && elapsed >= 0 && elapsed <= (snapshot?.process?.freshness_seconds || 5);
  }
  function equipment(id) {
    const raw = rawEquipment(id), fresh = isFresh(raw);
    if (!online) return {...raw, state:'stale', status_label:'연결 확인', fresh:false, reason:'서버 연결 실패 · 마지막 관측 ' + fullTime(raw.observed_at)};
    if (id === 'camera' && raw.state === 'stopped' && isFresh(rawEquipment('conveyor'))) return {...raw, fresh:false};
    if (!fresh && raw.state !== 'waiting') return {...raw, state:'stale', status_label:'관측 지연', fresh:false, reason:'최근 관측 갱신 필요 · 마지막 관측 ' + fullTime(raw.observed_at)};
    return {...raw, fresh};
  }
  function canControl() {
    return online && snapshot?.process?.mqtt_connected === true && isFresh(rawEquipment('motor')) && isFresh(rawEquipment('conveyor'));
  }
  function updateControls() {
    const allowed = canControl();
    $('fault').disabled = !allowed || commandBusy;
    $('apply').disabled = !allowed || commandBusy;
    $('reset').disabled = !allowed || commandBusy || snapshot?.process?.reset_ready !== true;
    text('resetHint', !allowed ? '최근 센서·라인 관측과 MQTT 연결을 확인한 뒤 제어할 수 있습니다.' : (snapshot.process.reset_reason || '고장 레벨 0 · HI ≥ 80에서 수동 재시작됩니다.'));
  }

  function selectEquipment(id, focusTab=false) {
    if (!ids.includes(id)) return;
    selectedEquipment = id;
    ids.forEach(item => {
      const active = item === id, suffix = cap(item);
      $('map' + suffix).classList.toggle('is-selected', active);
      $('map' + suffix).setAttribute('aria-pressed', String(active));
      $('card' + suffix).classList.toggle('is-selected', active);
      $('card' + suffix).setAttribute('aria-pressed', String(active));
      $('tab' + suffix).setAttribute('aria-selected', String(active));
      $('tab' + suffix).tabIndex = active ? 0 : -1;
      $(item + 'Detail').hidden = !active;
    });
    text('detailHeading', labels[id]);
    text('detailCode', codes[id] + ' · EQUIPMENT DETAIL');
    if (focusTab) $('tab' + cap(id)).focus();
    renderDetail();
    drawCharts();
  }

  function renderStatus() {
    if (!snapshot) return;
    const process = snapshot.process, motor = equipment('motor'), conveyor = equipment('conveyor'), camera = equipment('camera');
    const m = motor.metrics || {}, c = conveyor.metrics || {}, h = snapshot.latest_health;
    const isCurrent = motor.fresh && conveyor.fresh;
    const connectionState = !online ? 'offline' : !isCurrent || !process.mqtt_connected ? 'attention' : 'online';
    $('connection').className = 'connection ' + connectionState;
    $('connection').replaceChildren(Object.assign(document.createElement('i'), {}), document.createTextNode(!online ? '서버 연결 끊김' : !process.mqtt_connected ? 'MQTT 연결 확인' : !isCurrent ? '관측 갱신 확인' : '라이브 연결'));
    text('updatedAt', '갱신 ' + time(snapshot.timestamp));
    let pState = process.state, pLabel = process.label;
    if (!online) { pState='stale'; pLabel='연결 끊김 · 마지막 관측'; }
    else if (motor.state === 'stale' || conveyor.state === 'stale') { pState='stale'; pLabel='관측 갱신 확인'; }
    else if (camera.state === 'stale') { pState='attention'; pLabel='검사 갱신 확인'; }
    badge('processStatus', pState, pLabel);
    const notice = $('systemNotice');
    notice.hidden = online && isCurrent && process.mqtt_connected;
    notice.classList.toggle('offline', !online);
    notice.textContent = !online ? '서버에 연결하지 못했습니다. 마지막 관측값과 이력을 표시하며 라인 제어를 잠급니다. 갱신 시각: ' + fullTime(snapshot.timestamp) :
      !process.mqtt_connected ? 'MQTT 연결을 확인하고 있습니다. 라인 명령 전송이 잠겨 있습니다.' : '최근 5초 이내의 센서·라인 관측을 기다리고 있습니다. 오래된 값은 마지막 관측값입니다.';

    text('hi', fmt(m.health_index));
    badge('healthStatus', motor.state, motor.status_label);
    $('healthBar').style.width = num(m.health_index) === null ? '0%' : Math.max(0, Math.min(100, m.health_index))+'%';
    $('healthBar').style.background = !motor.fresh ? 'var(--gray)' : motor.state === 'normal' ? 'var(--green)' : motor.state === 'danger' ? 'var(--red)' : 'var(--amber)';
    text('rms', fmt(m.vibration_rms, 3));
    text('temperature', '온도 ' + fmt(m.temperature) + ' °C');
    const counts = snapshot.counts || {};
    text('rate', num(counts.inspections) && num(counts.defects) !== null ? fmt(100*counts.defects/counts.inspections) : '—');
    text('count', integer(counts.inspections) + '개 검사 · ' + integer(counts.defects) + '개 불량');
    text('line', conveyor.state === 'running' ? '가동' : conveyor.state === 'stopped' ? '정지' : conveyor.state === 'waiting' ? '대기' : conveyor.state === 'stale' ? '관측 지연' : conveyor.status_label);
    $('line').classList.toggle('long-label', $('line').textContent.length > 4);
    text('roller', '롤러 ' + fmt(c.roller_velocity, 3) + ' rad/s');
    $('lineStateDot').className = 'state-dot state-' + conveyor.state;
    document.querySelectorAll('.kpi').forEach((element, index) => element.classList.toggle('is-stale', !online || (index < 2 && !motor.fresh) || (index === 3 && !conveyor.fresh)));
    $('processMap').classList.toggle('is-running', online && conveyor.fresh && conveyor.state === 'running');
    [motor, conveyor, camera].forEach(item => {
      const suffix = cap(item.id), map = $('map' + suffix);
      map.classList.forEach(value => { if (value.startsWith('state-')) map.classList.remove(value); });
      map.classList.add('state-' + item.state);
      text('map' + suffix + 'Status', item.status_label);
      badge('card' + suffix + 'State', item.state, item.status_label);
      text('card' + suffix + 'Reason', item.reason || '관측 대기');
    });
    text('cardMotorMetric', 'HI ' + fmt(m.health_index) + ' · RMS ' + fmt(m.vibration_rms, 3));
    text('cardConveyorMetric', '롤러 ' + fmt(c.roller_velocity, 3) + ' rad/s · 레벨 ' + fmt(c.fault_level, 0));
    text('cardCameraMetric', camera.metrics?.last_inspection_at ? '마지막 검사 ' + time(camera.metrics.last_inspection_at) : '검사 영상 대기');
    if (!sliderTouched && num(c.fault_level) !== null) {
      $('fault').value = String(Math.round(c.fault_level));
      text('faultDisplay', $('fault').value + ' / 10');
    }
    metric('motorRpm', m.rpm, 0, 'RPM');
    metric('motorTemp', m.temperature, 1, '°C');
    metric('motorLatency', m.inference_ms, 2, 'ms');
    metric('conveyorVelocity', c.roller_velocity, 3, 'rad/s');
    metric('appliedFault', c.fault_level, 0, '/ 10');
    metric('conveyorRpm', c.motor_rpm, 0, 'RPM');
    text('interlockTitle', conveyor.state === 'running' ? '롤러 회전 관측' : conveyor.state === 'stopped' ? '정지 확인 · 수동 재시작 조건' : '라인 관측 확인');
    text('interlockReason', process.reset_reason || conveyor.reason);
    $('checkFault').classList.toggle('passed', conveyor.fresh && c.fault_level === 0);
    $('checkHealth').classList.toggle('passed', motor.fresh && num(m.health_index) !== null && m.health_index >= 80);
    $('checkFresh').classList.toggle('passed', motor.fresh && conveyor.fresh);
    renderDetail();
    updateControls();
    observeCommand();
    if (h?.spectrum) text('frequency', 'BPFO ' + fmt(h.spectrum.bpfo_hz) + ' / BPFI ' + fmt(h.spectrum.bpfi_hz) + ' Hz');
  }
  function renderDetail() {
    const current = equipment(selectedEquipment);
    badge('detailStatus', current.state, current.status_label);
    text('detailReason', current.reason || '관측 데이터 수집 중');
  }

  function imageUrl(path) {
    if (typeof path !== 'string' || !path || path.startsWith('/') || path.includes('\\')) return null;
    const segments = path.split('/');
    if (segments.some(segment => !segment || segment === '.' || segment === '..')) return null;
    return '/images/' + segments.map(encodeURIComponent).join('/');
  }
  function inspectionEvents() {
    return Array.isArray(snapshot?.inspection_events) ? snapshot.inspection_events : [];
  }
  function detectionDescription(item) {
    const detections = item?.detections || [];
    return detections.length ? [...new Set(detections.map(d => names[d.defect_type] || '결함'))].join(' · ') : '결함 미검출';
  }
  function showInspection(item) {
    displayedInspection = item || null;
    $('latestImage').hidden = selectedInspectionId === null;
    text('evidenceType', item ? detectionDescription(item) : '검사 영상 대기');
    text('evidenceTime', item ? (selectedInspectionId ? '선택한 검사 · ' : '최신 검사 · ') + fullTime(item.timestamp) : '');
    const image = $('inspection'), cam = $('cam'), url = imageUrl(item?.image_path), camUrl = imageUrl(item?.gradcam_path);
    if (!url) {
      currentImagePath=''; image.removeAttribute('src'); image.hidden=true;
      $('detectionOverlay').hidden=true; $('imagePlaceholder').hidden=false;
      text('imagePlaceholder', item ? '검사 영상 경로를 확인할 수 없습니다.' : '아직 검사 영상이 없습니다.');
    } else if (url !== currentImagePath) {
      currentImagePath=url; image.hidden=true; $('detectionOverlay').hidden=true;
      $('imagePlaceholder').hidden=false; text('imagePlaceholder', '검사 영상을 불러오는 중입니다.');
      image.src=url;
    } else if (image.complete && image.naturalWidth) drawDetections();
    if (!camUrl) {
      currentCamPath=''; cam.removeAttribute('src'); cam.hidden=true;
      $('camPlaceholder').hidden=false;
      text('camPlaceholder', item?.detections?.length ? '선택한 검사의 Grad-CAM 생성 대기' : '결함 검출 검사에서 Grad-CAM이 생성됩니다.');
    } else if (camUrl !== currentCamPath) {
      currentCamPath=camUrl; cam.hidden=true; $('camPlaceholder').hidden=false;
      text('camPlaceholder', 'Grad-CAM을 불러오는 중입니다.'); cam.src=camUrl;
    }
  }
  function drawDetections() {
    const image = $('inspection'), overlay = $('detectionOverlay'), namespace = 'http://www.w3.org/2000/svg';
    overlay.replaceChildren();
    if (!displayedInspection || !image.naturalWidth || image.hidden) { overlay.hidden=true; return; }
    const width=image.naturalWidth, height=image.naturalHeight;
    overlay.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    overlay.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    for (const detection of displayedInspection.detections || []) {
      const box=detection.bbox;
      if (!Array.isArray(box) || box.length !== 4 || box.some(value => num(value) === null)) continue;
      const x1=Math.max(0,Math.min(width,box[0])), y1=Math.max(0,Math.min(height,box[1]));
      const x2=Math.max(0,Math.min(width,box[2])), y2=Math.max(0,Math.min(height,box[3]));
      if (x2 <= x1 || y2 <= y1) continue;
      const rect=document.createElementNS(namespace,'rect');
      Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1,class:'detection-box'}).forEach(([key,value]) => rect.setAttribute(key,String(value)));
      const label=(names[detection.defect_type] || '결함') + ' ' + fmt(num(detection.confidence) === null ? null : detection.confidence*100) + '%';
      const textWidth=Math.min(width,Math.max(110,label.length*12));
      const labelX=Math.min(x1,Math.max(0,width-textWidth)), labelY=Math.max(0,y1-23);
      const background=document.createElementNS(namespace,'rect');
      Object.entries({x:labelX,y:labelY,width:textWidth,height:23,rx:2,class:'detection-label-bg'}).forEach(([key,value]) => background.setAttribute(key,String(value)));
      const caption=document.createElementNS(namespace,'text');
      caption.setAttribute('x',String(labelX+6)); caption.setAttribute('y',String(labelY+16)); caption.setAttribute('class','detection-label'); caption.textContent=label;
      overlay.append(rect,background,caption);
    }
    overlay.hidden = !overlay.childElementCount;
  }
  function renderInspections() {
    const events=inspectionEvents(), fragment=document.createDocumentFragment();
    const active = $('history').contains(document.activeElement) ? document.activeElement : null;
    const focusedEventId = active?.dataset.eventId;
    let focusReplacement = null;
    if (!events.length) {
      const row=document.createElement('tr'), cell=document.createElement('td');
      cell.colSpan=5; cell.className='empty-table'; cell.textContent='검사 결과를 기다리고 있습니다.'; row.append(cell); fragment.append(row);
    }
    events.forEach(item => {
      const row=document.createElement('tr');
      row.classList.toggle('selected-row', selectedInspectionId === item.event_id);
      const stamp=document.createElement('td'); stamp.textContent=time(item.timestamp); stamp.title=fullTime(item.timestamp);
      const result=document.createElement('td'), tag=document.createElement('span');
      tag.className='status-badge state-' + (item.defective ? 'warning' : 'normal'); tag.textContent=detectionDescription(item); result.append(tag);
      const confidence=document.createElement('td'), values=(item.detections || []).map(d => num(d.confidence)).filter(v => v !== null);
      confidence.textContent=values.length ? fmt(Math.max(...values)*100)+'%' : '—';
      const health=document.createElement('td'); health.textContent=fmt(item.health_index_at_time);
      const action=document.createElement('td'), button=document.createElement('button');
      button.type='button'; button.className='text-button'; button.textContent='상세 보기 ↗';
      button.dataset.eventId = item.event_id;
      if (item.event_id === focusedEventId) focusReplacement = button;
      button.setAttribute('aria-label', fullTime(item.timestamp)+' 검사 상세 보기');
      button.onclick=() => {
        selectedInspectionId=item.event_id; selectedInspection=item;
        selectEquipment('camera'); showInspection(item); renderInspections();
        $('detailHeading').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
        $('tabCamera').focus({preventScroll:true});
      };
      action.append(button); row.append(stamp,result,confidence,health,action); fragment.append(row);
    });
    $('history').replaceChildren(fragment);
    if (focusedEventId) (focusReplacement || $('historyHeading')).focus({preventScroll:true});
    if (selectedInspectionId) {
      selectedInspection=events.find(item => item.event_id === selectedInspectionId) || selectedInspection;
      showInspection(selectedInspection);
    } else showInspection(events[0]);
  }
  function renderEvents() {
    const events=Array.isArray(snapshot.process.recent_events) ? snapshot.process.recent_events : [], fragment=document.createDocumentFragment();
    events.forEach(item => {
      const entry=document.createElement('article'), stamp=document.createElement('time'), body=document.createElement('div'), source=document.createElement('strong'), message=document.createElement('p');
      entry.className='event-entry state-' + (stateNames.has(item.severity) ? item.severity : 'waiting');
      stamp.textContent=time(item.timestamp); if (item.timestamp) stamp.dateTime=item.timestamp; stamp.title=fullTime(item.timestamp);
      source.textContent=sourceNames[item.source] || '시스템'; message.textContent=item.message || '관측 기록';
      body.append(source,message); entry.append(stamp,body); fragment.append(entry);
    });
    if (!events.length) { const empty=document.createElement('p'); empty.className='empty-log'; empty.textContent='아직 관제 기록이 없습니다.'; fragment.append(empty); }
    $('events').replaceChildren(fragment); text('eventCount', events.length+' EVENTS');
  }

  function chart(id, series, options={}) {
    const canvas=$(id), rect=canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const scale=window.devicePixelRatio || 1, w=rect.width, h=rect.height;
    canvas.width=Math.round(w*scale); canvas.height=Math.round(h*scale);
    const context=canvas.getContext('2d'); context.scale(scale,scale);
    const left=38,right=13,top=12,bottom=25, plotWidth=w-left-right,plotHeight=h-top-bottom;
    const xmax=Math.max(.001,options.xmax || 100), ymax=Math.max(.001,options.ymax || 100);
    const x=value => left+value/xmax*plotWidth, y=value => top+plotHeight*(1-value/ymax);
    context.font='9px Segoe UI, sans-serif'; context.strokeStyle='#e8eef4'; context.fillStyle='#90a1b3'; context.lineWidth=1;
    for (let i=0;i<5;i++) {
      const value=ymax*(1-i/4), py=top+plotHeight*i/4;
      context.beginPath(); context.moveTo(left,py); context.lineTo(w-right,py); context.stroke();
      context.fillText(value.toFixed(ymax<=1?2:ymax<3?1:0),0,py+3);
    }
    series.forEach(item => {
      context.strokeStyle=item.color; context.fillStyle=item.color; context.lineWidth=1.8;
      context.beginPath(); let open=false;
      item.points.forEach(point => {
        if (num(point[0]) === null || num(point[1]) === null) { open=false; return; }
        const px=x(point[0]), py=y(point[1]);
        if (options.scatter) { context.moveTo(px+3,py); context.arc(px,py,3,0,Math.PI*2); }
        else { if (open) context.lineTo(px,py); else context.moveTo(px,py); open=true; }
      });
      options.scatter ? context.fill() : context.stroke();
    });
    context.fillStyle='#90a1b3'; context.textAlign='left'; context.fillText(options.leftLabel ?? '0',left,h-4);
    context.textAlign='right'; context.fillText(options.rightLabel ?? String(xmax),w-right,h-4); context.textAlign='left';
  }
  function drawCharts() {
    if (!snapshot) return;
    const rows=(snapshot.rows || []).slice(-120), spectrum=snapshot.latest_health?.spectrum;
    const validRows=rows.filter(item => num(item.vibration_x) !== null || num(item.health_index) !== null);
    chart('trend',[{color:'#279777',points:rows.map((item,i) => [i,num(item.health_index)])},{color:'#5a8bc5',points:rows.map((item,i) => [i,num(item.vibration_x) === null ? null : item.vibration_x*100])}],{xmax:Math.max(1,rows.length-1),ymax:120,leftLabel:time(rows[0]?.timestamp),rightLabel:time(rows.at(-1)?.timestamp)});
    $('trendEmpty').hidden=validRows.length>0;
    text('trendRange', rows.length ? '최근 '+rows.length+'개 센서 표본 · '+time(rows[0].timestamp)+' – '+time(rows.at(-1).timestamp)+(equipment('motor').fresh ? '' : ' · 마지막 관측') : '최근 120개 센서 표본 · 시간 순서');
    const frequencies=Array.isArray(spectrum?.frequencies) ? spectrum.frequencies : [], amplitudes=Array.isArray(spectrum?.amplitudes) ? spectrum.amplitudes : [];
    const fftPoints=frequencies.map((frequency,i) => [num(frequency),num(amplitudes[i])]);
    chart('fft',[{color:'#5a8bc5',points:fftPoints}],{xmax:Math.max(400,...frequencies.filter(value => num(value)!==null)),ymax:Math.max(.2,...amplitudes.filter(value => num(value)!==null))*1.1});
    $('fftEmpty').hidden=fftPoints.some(point => point.every(value => value!==null));
    const association=snapshot.correlation || {}, buckets=Array.isArray(association.buckets) ? association.buckets : [];
    chart('scatter',[{color:'#bf9445',points:buckets.map(item => [num(item.rms),num(item.defect_rate)])}],{xmax:Math.max(1,...buckets.map(item => num(item.rms) || 0)),ymax:1,scatter:true});
    $('scatterEmpty').hidden=buckets.length>0;
    text('corr', 'Pearson '+fmt(association.pearson,3)+' · Spearman '+fmt(association.spearman,3));
    const lag=(association.lags || []).filter(item => num(item.pearson)!==null).sort((a,b) => Math.abs(b.pearson)-Math.abs(a.pearson))[0];
    text('lag', lag ? '최대 |r| 시차 '+lag.seconds+'초 · r '+fmt(lag.pearson,3)+' · '+lag.pairs+'개 구간 (5초 평균 RMS / 불량률)' : '5초 구간 평균 RMS / 불량률 · 미검사 / 정지 구간 제외');
  }

  function commandNotice(message, kind='') { text('notice',message); $('notice').className='command-notice'+(kind?' '+kind:''); }
  function observeCommand() {
    if (!pendingCommand || !snapshot) return;
    const line=rawEquipment('conveyor'), stamp=line.observed_at;
    const newer=stamp && (!pendingCommand.beforeStamp || Date.parse(stamp) > Date.parse(pendingCommand.beforeStamp));
    if (online && isFresh(line) && newer && (pendingCommand.kind==='fault' ? line.metrics.fault_level===pendingCommand.level : line.state==='running')) {
      commandNotice(pendingCommand.kind==='fault' ? '관측 확인 · 고장 레벨 '+pendingCommand.level+' 적용됨' : '관측 확인 · 컨베이어 롤러 가동', 'success'); pendingCommand=null;
    } else if (performance.now()-pendingCommand.startedAt > 15000) {
      commandNotice('요청은 접수됐지만 15초 안에 적용 관측을 확인하지 못했습니다. 현재 라인 상태와 관제 기록을 확인하세요.'); pendingCommand=null;
    }
  }
  async function sendCommand(kind) {
    if (!canControl() || commandBusy || (kind==='reset' && !snapshot.process.reset_ready)) return;
    commandBusy=true; updateControls();
    const controller=new AbortController(), timeout=setTimeout(() => controller.abort(),6500);
    const level=Number($('fault').value), beforeStamp=rawEquipment('conveyor').observed_at, sentAt=Date.now();
    commandNotice(kind==='fault' ? '고장 레벨 '+level+' 전송 중…' : '안전 재시작 요청 중…');
    try {
      const response=await fetch(kind==='fault'?'/api/fault':'/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},...(kind==='fault'?{body:JSON.stringify({fault_level:level})}:{}),signal:controller.signal});
      let data; try { data=await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다.'); }
      if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'명령 요청 실패 (HTTP '+response.status+')');
      if (data.accepted !== true) throw new Error('명령 접수 여부를 확인하지 못했습니다.');
      pendingCommand={kind,level,beforeStamp,sentAt,startedAt:performance.now()};
      commandNotice(kind==='fault' ? '고장 레벨 '+level+' 요청 접수 · 실제 라인 관측에서 적용 확인 중' : '재시작 요청 접수 · 실제 롤러 가동 관측을 기다립니다.');
    } catch (error) {
      pendingCommand=null;
      commandNotice(error.name==='AbortError' ? '요청 응답이 지연되어 접수 여부가 불확실합니다. 라인 관측과 관제 기록을 먼저 확인하세요.' : error.message, 'error');
    } finally { clearTimeout(timeout); commandBusy=false; updateControls(); }
  }

  async function refresh() {
    const controller=new AbortController(), timeout=setTimeout(() => controller.abort(),6500);
    try {
      const response=await fetch('/api/snapshot',{cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error('Snapshot HTTP '+response.status);
      const data=await response.json();
      if (!data.process || !Array.isArray(data.process.equipment)) throw new Error('Invalid process snapshot');
      snapshot=data; receivedAt=performance.now(); online=true;
      renderStatus(); renderInspections(); renderEvents(); drawCharts();
    } catch {
      online=false;
      if (snapshot) { renderStatus(); drawCharts(); }
      else {
        $('connection').className='connection offline'; text('connection','서버 연결 대기');
        $('systemNotice').hidden=false; $('systemNotice').className='system-notice offline';
        text('systemNotice','관제 서버에 연결하지 못했습니다. 서비스가 실행되면 자동으로 연결합니다.');
        badge('processStatus','waiting','공정 데이터 대기'); updateControls();
      }
    } finally { clearTimeout(timeout); refreshTimer=setTimeout(refresh,2000); }
  }

  document.querySelectorAll('[data-equipment]').forEach(element => {
    element.addEventListener('click',() => selectEquipment(element.dataset.equipment));
    if (element.tagName.toLowerCase()==='g') element.addEventListener('keydown',event => {
      if (event.key==='Enter' || event.key===' ') { event.preventDefault(); selectEquipment(element.dataset.equipment); }
    });
    if (element.getAttribute('role')==='tab') element.addEventListener('keydown',event => {
      const position=ids.indexOf(selectedEquipment);
      let target=null;
      if (event.key==='ArrowRight') target=ids[(position+1)%3];
      if (event.key==='ArrowLeft') target=ids[(position+2)%3];
      if (event.key==='Home') target=ids[0];
      if (event.key==='End') target=ids[2];
      if (target) { event.preventDefault(); selectEquipment(target,true); }
    });
  });
  $('fault').addEventListener('input',() => {sliderTouched=true; text('faultDisplay',$('fault').value+' / 10');});
  $('apply').addEventListener('click',() => sendCommand('fault'));
  $('reset').addEventListener('click',() => sendCommand('reset'));
  $('latestImage').addEventListener('click',() => {selectedInspectionId=null; selectedInspection=null; renderInspections();});
  $('inspection').addEventListener('load',() => {$('inspection').hidden=false; $('imagePlaceholder').hidden=true; drawDetections();});
  $('inspection').addEventListener('error',() => {currentImagePath=''; $('inspection').hidden=true; $('detectionOverlay').hidden=true; $('imagePlaceholder').hidden=false; text('imagePlaceholder','검사 영상을 불러오지 못했습니다.');});
  $('cam').addEventListener('load',() => {$('cam').hidden=false; $('camPlaceholder').hidden=true;});
  $('cam').addEventListener('error',() => {currentCamPath=''; $('cam').hidden=true; $('camPlaceholder').hidden=false; text('camPlaceholder','Grad-CAM 영상을 불러오지 못했습니다.');});
  let resizeTimer;
  window.addEventListener('resize',() => {clearTimeout(resizeTimer); resizeTimer=setTimeout(drawCharts,120);});
  document.addEventListener('visibilitychange',() => { if (!document.hidden) {renderStatus(); drawCharts();} });
  setInterval(() => {
    if (!snapshot) return;
    const signature=ids.map(id => equipment(id).state).join(':')+':'+online;
    if (signature!==staleSignature) {staleSignature=signature;renderStatus();drawCharts();}
    observeCommand(); updateControls();
  },500);
  refresh();
})();
