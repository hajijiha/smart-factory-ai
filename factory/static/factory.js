/* Gazebo is the source of motion. Interpolation is bounded by two observations. */
const $ = id => document.getElementById(id);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fmt = (value, digits=1) => finite(value) ? value.toFixed(digits) : '—';
const text = (id, value) => { $(id).textContent = value; };
const localTime = stamp => { const date=new Date(stamp); return stamp && Number.isFinite(date.getTime()) ? date.toLocaleTimeString('ko-KR',{hour12:false,timeZone:'Asia/Seoul'}) : '—'; };
const stages = {infeed:'투입',transport:'이송',conveying:'이송',inspection:'검사',inspecting:'검사',inspected:'검사 완료',sorting:'분류',outfeed:'배출',discharged:'배출 완료',recycling:'재투입'};
const equipmentNames = {conveyor:'컨베이어',motor:'진단 모터',camera:'검사셀'};
const shortProductId = id => String(id).split('-').at(-1);
let THREE, OrbitControls, renderer, scene, camera, controls, layout, meshes=new Map(), labels=new Map();
let envelope=null, latest=null, samples=[], retiredRuns=new Set(), lastSequence=-1, worldReceived=0, lastSampleAt=0;
let snapshot=null, snapshotReceived=0, products=[], selected='conveyor', follow=false, pending=null, busy=false;
let socket=null, socketOpen=false, paused=false, disposed=false, rendererLost=false, animationId, resizeObserver;
let reconnectTimer, worldTimer, snapshotTimer, productTimer, uiTimer, layoutRequest=null;
let frames=0, fpsStarted=0, fps=null, slowWindows=0, qualityReduced=false, latencySamples=[];
let observations=[], requestControllers=new Set(), lastProductSignature='', cameraSources=new Map();
let displayedSequence=null, displayedRun=null, displayedAt=null, displayedDelay=null;
const query=new URLSearchParams(location.search);
if (equipmentNames[query.get('equipment')]) selected=query.get('equipment');

async function getJSON(url) {
  const abort=new AbortController(); requestControllers.add(abort);
  const timeout=setTimeout(()=>abort.abort(),6500);
  try { const response=await fetch(url,{cache:'no-store',signal:abort.signal}); if(!response.ok) throw new Error('HTTP '+response.status); return await response.json(); }
  finally { clearTimeout(timeout); requestControllers.delete(abort); }
}
function worldFresh() {
  return !paused && envelope?.connected===true && envelope.stale!==true && !!latest &&
    finite(envelope.age_ms) && envelope.age_ms+Math.max(0,performance.now()-worldReceived)<1500 &&
    performance.now()-lastSampleAt<1500;
}
function observedEquipment(id) { return snapshot?.process?.equipment?.find(item=>item.id===id); }
function equipmentFresh(item) {
  const limit=item?.freshness_seconds||snapshot?.process?.freshness_seconds||5;
  return item?.fresh===true && finite(item.age_seconds) && item.age_seconds+(performance.now()-snapshotReceived)/1000<=limit;
}
function lineMeasurements() {
  const joints=latest?.joints||[];
  const rollers=joints.filter(joint=>String(joint.id).includes('roller'));
  const observed=rollers.length ? rollers : joints.filter(joint=>!String(joint.id).includes('motor'));
  const velocities=observed.map(joint=>joint.velocity).filter(finite);
  const roller=velocities.length ? Math.max(...velocities.map(Math.abs)) : observedEquipment('conveyor')?.metrics?.roller_velocity;
  const plates=(latest?.objects||[]).filter(object=>object.kind==='product');
  const speeds=plates.map(object=>Array.isArray(object.velocity)&&object.velocity.length>=3&&object.velocity.every(finite) ? Math.hypot(...object.velocity.slice(0,3)) : null).filter(finite);
  return {roller,productSpeed:speeds.length?Math.max(...speeds):null,plates};
}
function lineStatus() {
  if (!worldFresh()) return {label:latest?'관측 지연':'관측 대기',state:'stale'};
  const {roller,productSpeed}=lineMeasurements();
  if (latest.inspection_hold===true && latest.running===true) return {label:'검사 대기',state:'warning'};
  if (latest.running===true && finite(roller) && roller>.01) return {label:'가동',state:'running'};
  if (latest.running===false && finite(roller) && roller<=.01) return {label:finite(productSpeed)&&productSpeed>.03?'잔류 이송':'정지 확인',state:finite(productSpeed)&&productSpeed>.03?'warning':'stopped'};
  return {label:latest.running===false?'감속 중':'가동 확인 중',state:'warning'};
}
function badge(id,state,label) { $(id).className='status-badge state-'+state; text(id,label); }
function verdictFor(object,runId=latest?.run_id) {
  if (!object) return null;
  const identity=object.product_id||object.id;
  return products.find(product=>product.run_id===runId && product.product_id===identity &&
    object.inspection_id && product.inspection_id===object.inspection_id && product.quality && product.outcome!=='unknown')||null;
}
function acceptWorld(data) {
  if (paused||disposed||!data||typeof data!=='object') return;
  const state=data.state||data;
  if (!state.run_id || !Number.isInteger(state.sequence) || !Array.isArray(state.objects)) { envelope=data;worldReceived=performance.now();renderUI();return; }
  if(latest&&state.run_id!==latest.run_id&&retiredRuns.has(state.run_id))return;
  if(latest&&state.run_id===latest.run_id&&state.sequence<lastSequence)return;
  envelope=data; worldReceived=performance.now();
  if (latest && state.run_id!==latest.run_id) {
    if (retiredRuns.has(state.run_id)) return;
    retiredRuns.add(latest.run_id); if(retiredRuns.size>32) retiredRuns.delete(retiredRuns.values().next().value);
    samples=[]; lastSequence=-1; observations=[]; latencySamples=[]; displayedSequence=null;displayedRun=null;pending=null;products=[];lastProductSignature='';
    text('commandNotice','새 시뮬레이션 실행에 연결했습니다. 제품·검사 상태를 다시 관측합니다.');
    pollProducts();
  }
  if(state.sequence<=lastSequence) { renderUI(); return; }
  const now=performance.now(), wasFresh=!!latest&&now-lastSampleAt<1500;
  latest=state; lastSequence=state.sequence; lastSampleAt=now;
  samples.push({state,at:now,sourceAge:finite(data.age_ms)?data.age_ms:null}); if(!wasFresh) samples=samples.slice(-1); if(samples.length>5) samples.shift();
  observations.push(now); observations=observations.filter(at=>now-at<2500);
  if(layout?.world_version!==state.world_version) loadLayout();
  updateProductColors(); renderUI(); observeCommand();
}
async function loadLayout() {
  if(layoutRequest||disposed) return layoutRequest;
  layoutRequest=getJSON('/api/world/layout').then(data=>{
    if(!Array.isArray(data.objects)) throw new Error('Invalid world layout');
    layout=data; if(scene) buildWorld();
  }).catch(()=>{text('viewportState','공장 배치 연결 대기');}).finally(()=>{layoutRequest=null;});
  return layoutRequest;
}
function connectWorld() {
  if(paused||disposed||socket) return;
  const protocol=location.protocol==='https:'?'wss:':'ws:';
  const connection=new WebSocket(protocol+'//'+location.host+'/ws/world');socket=connection;
  connection.addEventListener('open',()=>{if(socket===connection)socketOpen=true;});
  connection.addEventListener('message',event=>{if(socket!==connection)return;try{acceptWorld(JSON.parse(event.data));}catch{ text('viewportState','관측 형식 확인'); }});
  connection.addEventListener('close',()=>{
    if(socket!==connection)return;
    socket=null; socketOpen=false;
    if(!paused&&!disposed) { worldReceived=-Infinity; renderUI(); pollWorld(); reconnectTimer=setTimeout(connectWorld,2000); }
  });
  connection.addEventListener('error',()=>connection.close());
}
async function pollWorld() {
  clearTimeout(worldTimer); if(paused||disposed) return;
  if(!socketOpen) { try{acceptWorld(await getJSON('/api/world'));}catch{worldReceived=-Infinity; renderUI();} }
  if(!paused&&!disposed)worldTimer=setTimeout(pollWorld,1000);
}
async function pollSnapshot() {
  clearTimeout(snapshotTimer); if(paused||disposed) return;
  try{snapshot=await getJSON('/api/snapshot');snapshotReceived=performance.now();renderUI();}catch{snapshotReceived=0;renderUI();}
  if(!paused&&!disposed)snapshotTimer=setTimeout(pollSnapshot,1800);
}
async function pollProducts() {
  clearTimeout(productTimer); if(paused||disposed) return;
  try{const data=await getJSON('/api/products');products=Array.isArray(data.products)?data.products:[];updateProductColors();renderProducts();renderSelection();}catch{/* Keep the last identified results. */}
  if(!paused&&!disposed)productTimer=setTimeout(pollProducts,1500);
}

function disposeWorld() {
  for(const object of meshes.values()) { scene?.remove(object); object.traverse(child=>{child.geometry?.dispose();if(Array.isArray(child.material))child.material.forEach(material=>material.dispose());else child.material?.dispose();}); }
  meshes.clear(); labels.clear(); $('worldLabels').replaceChildren();
}
function buildWorld() {
  disposeWorld();
  for(const spec of layout.objects) {
    if(!spec.id||!Array.isArray(spec.size)||!spec.size.every(finite)||!Array.isArray(spec.position)||!spec.position.every(finite))continue;
    let geometry;
    if(spec.shape==='cylinder') {geometry=new THREE.CylinderGeometry(spec.size[0],spec.size[0],spec.size[1],20);geometry.rotateX(Math.PI/2);}
    else if(spec.shape==='box')geometry=new THREE.BoxGeometry(...spec.size.slice(0,3));
    else continue;
    const group=new THREE.Group(); group.userData={...spec};
    const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:spec.color||'#a8b8c6',roughness:.78,metalness:spec.kind==='roller'?.45:.13}));
    group.add(mesh); group.position.fromArray(spec.position); if(spec.rotation?.length===3)group.rotation.set(...spec.rotation);
    if(spec.kind==='roller') {
      const mark=new THREE.Mesh(new THREE.BoxGeometry(spec.size[0]*.22,spec.size[0]*.10,spec.size[1]*.94),new THREE.MeshStandardMaterial({color:'#2c607c',roughness:.85}));
      mark.position.set(spec.size[0]*.98,0,0); group.add(mark);
    }
    if(spec.kind==='product')group.visible=false;
    scene.add(group);meshes.set(spec.id,group);
    if(['product','motor','camera'].includes(spec.kind)) {
      const label=document.createElement('span');label.className='world-label'+(spec.kind==='product'?' product-label':'');
      label.textContent=spec.kind==='product'?spec.id:spec.kind==='motor'?'진단 모터':'검사 카메라';
      $('worldLabels').append(label);labels.set(spec.id,label);
    }
  }
  const zoneNames={infeed:'01 · 투입',inspection:'02 · 검사',outfeed:'03 · 배출'};
  for(const zone of layout.zones||[]) {
    if(!zoneNames[zone.id]||zone.position?.length!==3)continue;
    const anchor=new THREE.Group();anchor.userData={id:'zone_'+zone.id,kind:'zone_label'};anchor.position.fromArray(zone.position);anchor.position.y-=.93;anchor.position.z=.12;
    scene.add(anchor);meshes.set(anchor.userData.id,anchor);
    const label=document.createElement('span');label.className='world-label zone-label';label.textContent=zoneNames[zone.id];$('worldLabels').append(label);labels.set(anchor.userData.id,label);
  }
  const lineSpecs=layout.objects.filter(object=>['roller','product'].includes(object.kind));
  const positions=lineSpecs.map(object=>object.position?.[0]).filter(finite);
  layout.lineCenter=positions.length?(Math.min(...positions)+Math.max(...positions))/2:0;
  layout.lineSpan=positions.length?Math.max(8,Math.max(...positions)-Math.min(...positions)):12;
  setView('overview'); if(latest)applyPose(latest,latest,1); updateProductColors();select(selected);
}
function cameraTarget(view) {
  const center=layout?.lineCenter||0, span=layout?.lineSpan||12;
  const motor=[...meshes.values()].find(object=>object.userData.kind==='motor');
  if(view==='top')return {target:[center,0,.3],position:[center,-.01,Math.max(11,span*.9)]};
  if(view==='inspection')return {target:[0,0,.7],position:[3.3,-4.2,3.5]};
  if(view==='motor'&&motor)return {target:motor.position.toArray(),position:[motor.position.x+2.5,motor.position.y-3.3,motor.position.z+2.4]};
  const scale=Math.max(1,1.9/(camera?.aspect||1.9));
  return {target:[center,0,.3],position:[center+span*.45*scale,-span*.63*scale,.3+span*.50*scale]};
}
function setView(view) {
  document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===view)));
  follow=false;$('follow').setAttribute('aria-pressed','false');
  if(!controls)return;
  const preset=cameraTarget(view);camera.position.fromArray(preset.position);controls.target.fromArray(preset.target);controls.update();
}
function applyPose(before,after,fraction) {
  const previous=new Map(before.objects.map(object=>[object.id,object]));
  const present=new Set();
  for(const observation of after.objects) {
    const object=meshes.get(observation.id); if(!object)continue;present.add(observation.id);
    const validPosition=observation.position?.length===3&&observation.position.every(finite);
    const validQuaternion=observation.quaternion?.length===4&&observation.quaternion.every(finite);
    if(!validPosition||!validQuaternion)continue;
    object.visible=true;
    object.userData.renderedObservation=observation;object.userData.renderedRun=after.run_id;
    const old=previous.get(observation.id), sameProduct=observation.kind!=='product'||old?.product_id===observation.product_id;
    const interpolate=sameProduct&&old?.position?.length===3&&old?.quaternion?.length===4;
    if(interpolate) {
      object.position.fromArray(old.position).lerp(new THREE.Vector3().fromArray(observation.position),fraction);
      object.quaternion.fromArray(old.quaternion).slerp(new THREE.Quaternion().fromArray(observation.quaternion),fraction);
    } else {object.position.fromArray(observation.position);object.quaternion.fromArray(observation.quaternion);}
    if(labels.has(observation.id)&&observation.kind==='product'){const label=labels.get(observation.id);label.textContent=shortProductId(observation.product_id||observation.id);label.title=observation.product_id||observation.id;}
  }
  for(const [id,object] of meshes)if(['product','roller'].includes(object.userData.kind)&&!present.has(id))object.visible=false;
  updateProductColors();
}
function updateProductColors() {
  if(!THREE)return;
  for(const object of meshes.values()) {
    const observation=object.userData.renderedObservation;
    if(object.userData.kind!=='product'||!observation)continue;
    const result=verdictFor(observation,object.userData.renderedRun), verdict=result?.outcome||'unknown';
    const key=[object.userData.renderedRun,observation.product_id||observation.id,observation.inspection_id,verdict,selected===observation.id].join('|');
    if(object.userData.appearanceKey===key)continue;object.userData.appearanceKey=key;
    object.children[0].material.color.set(verdict==='normal'?'#60aa87':verdict==='defective'?'#d46b77':object.userData.color||'#bcc9d4');
    if(labels.has(observation.id))labels.get(observation.id).className='world-label product-label '+verdict+(selected===observation.id?' selected':'');
  }
}
function renderLabels() {
  const rect=$('viewport').getBoundingClientRect();
  for(const [id,label] of labels) {
    const object=meshes.get(id);if(!object?.visible){label.hidden=true;continue;}
    const point=object.position.clone();point.z+=object.userData.kind==='product'?.14:object.userData.kind==='zone_label'?0:.43;point.project(camera);
    label.hidden=point.z< -1||point.z>1||Math.abs(point.x)>1||Math.abs(point.y)>1;
    if(!label.hidden){label.style.left=((point.x+1)*rect.width/2)+'px';label.style.top=((-point.y+1)*rect.height/2)+'px';}
  }
}
function animate(now) {
  if(paused||disposed)return;
  animationId=requestAnimationFrame(animate);
  if(!renderer||rendererLost)return;
  if(worldFresh()&&samples.length&&layout?.world_version===latest?.world_version) {
    const target=now-110;let before=samples[0],after=samples[0];
    for(const sample of samples){if(sample.at<=target)before=sample;if(sample.at>=target){after=sample;break;}after=sample;}
    const fraction=after.at>before.at?Math.max(0,Math.min(1,(target-before.at)/(after.at-before.at))):1;
    applyPose(before.state,after.state,fraction);
    const shown=fraction>0?after:before;
    displayedDelay=finite(before.sourceAge)&&finite(after.sourceAge)?
      (before.sourceAge+Math.max(0,now-before.at))*(1-fraction)+(after.sourceAge+Math.max(0,now-after.at))*fraction:null;
    if(displayedSequence!==shown.state.sequence||displayedRun!==shown.state.run_id) {
      displayedSequence=shown.state.sequence;displayedRun=shown.state.run_id;
      const beforeUTC=Date.parse(before.state.timestamp),afterUTC=Date.parse(after.state.timestamp);
      displayedAt=Number.isFinite(beforeUTC)&&Number.isFinite(afterUTC)?new Date(beforeUTC+(afterUTC-beforeUTC)*fraction).toISOString():shown.state.timestamp;
      if(finite(displayedDelay)){latencySamples.push(displayedDelay);if(latencySamples.length>180)latencySamples.shift();}
    }
  }
  if(follow&&meshes.has(selected)) {
    const position=meshes.get(selected).position;
    const delta=position.clone().sub(controls.target);camera.position.add(delta);controls.target.copy(position);
  }
  controls.update();renderer.render(scene,camera);renderLabels();frames++;
  if(now-fpsStarted>=1000) {
    fps=frames*1000/(now-fpsStarted);frames=0;fpsStarted=now;text('renderFps','화면 '+fmt(fps,0)+' FPS'+(qualityReduced?' · 저해상도':''));
    slowWindows=fps<22?slowWindows+1:0;
    if(slowWindows>=3&&!qualityReduced){renderer.setPixelRatio(.85);qualityReduced=true;resizeViewer();text('renderFps','화면 '+fmt(fps,0)+' FPS · 저해상도');}
  }
}
function resizeViewer() {
  if(!renderer)return;const {width,height}=$('viewport').getBoundingClientRect();
  const nextAspect=width/height, ratio=Math.max(1,1.9/nextAspect)/Math.max(1,1.9/camera.aspect);
  if(controls&&document.querySelector('[data-view="overview"]').getAttribute('aria-pressed')==='true')camera.position.sub(controls.target).multiplyScalar(ratio).add(controls.target);
  renderer.setSize(width,height,false);camera.aspect=nextAspect;camera.updateProjectionMatrix();
}
function viewerFallback(reason) {rendererLost=true;$('viewerFallback').hidden=false;text('fallbackReason',reason+' 아래 실제 Gazebo 전경 영상으로 라인을 확인할 수 있습니다.');text('renderFps','실제 카메라 대체 화면');}
async function initViewer() {
  try {
    const canvas=$('worldCanvas');
    if(!canvas.getContext('webgl2',{antialias:true}))throw new Error('이 브라우저에서 WebGL2를 사용할 수 없습니다.');
    THREE=await import('three');({OrbitControls}=await import('/static/vendor/three-r170/OrbitControls.js'));
    renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:false,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.3));renderer.outputColorSpace=THREE.SRGBColorSpace;
    scene=new THREE.Scene();scene.background=new THREE.Color('#e8eef4');
    camera=new THREE.PerspectiveCamera(43,$('viewport').clientWidth/$('viewport').clientHeight,.05,150);camera.up.set(0,0,1);
    controls=new OrbitControls(camera,canvas);controls.enableDamping=true;controls.dampingFactor=.10;controls.minDistance=1;controls.maxDistance=40;controls.maxPolarAngle=Math.PI*.485;
    scene.add(new THREE.HemisphereLight('#ffffff','#91a3b5',2.5));
    const sunlight=new THREE.DirectionalLight('#ffffff',2.4);sunlight.position.set(-5,-9,14);scene.add(sunlight);
    canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();viewerFallback('3D 그래픽 연결이 끊겼습니다.');});
    canvas.addEventListener('webglcontextrestored',()=>{rendererLost=false;$('viewerFallback').hidden=true;buildWorld();resizeViewer();});
    let down=null;
    canvas.addEventListener('pointerdown',event=>{down={x:event.clientX,y:event.clientY};});
    canvas.addEventListener('pointerup',event=>{
      if(!down||Math.hypot(event.clientX-down.x,event.clientY-down.y)>5)return;
      const rect=canvas.getBoundingClientRect(),pointer=new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),ray=new THREE.Raycaster();
      ray.setFromCamera(pointer,camera);const hit=ray.intersectObjects([...meshes.values()],true).find(hit=>['motor','drive_motor','camera','inspection_cell','roller','product','conveyor'].includes(hit.object.parent?.userData.kind));
      if(hit){const spec=hit.object.parent.userData;select(spec.kind==='product'?spec.id:['roller','drive_motor'].includes(spec.kind)?'conveyor':spec.kind==='inspection_cell'?'camera':spec.kind,true);}
    });
    resizeObserver=new ResizeObserver(resizeViewer);resizeObserver.observe($('viewport'));
    await loadLayout();if(layout)buildWorld();resizeViewer();fpsStarted=performance.now();animationId=requestAnimationFrame(animate);
  }catch(error){viewerFallback(error.message||'3D 자산을 불러오지 못했습니다.');}
}

function select(id,userAction=false) {
  selected=id;
  if(userAction&&matchMedia('(max-width:680px)').matches)$('selectionDetails').open=true;
  if(!meshes.has(id))follow=false;
  $('follow').setAttribute('aria-pressed',String(follow));
  document.querySelectorAll('[data-select]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.select===id)));
  for(const [meshId,object]of meshes) {
    const active=meshId===id||(id==='conveyor'&&['roller','drive_motor','conveyor'].includes(object.userData.kind));
    if(object.children[0]?.material?.emissive)object.children[0].material.emissive.set(active?'#103347':'#000000');
    if(labels.has(meshId)&&object.userData.kind!=='product')labels.get(meshId).classList.toggle('selected',active);
  }
  renderSelection();renderProducts(true);updateProductColors();
}
function renderSelection() {
  const product=(latest?.objects||[]).find(object=>object.id===selected&&object.kind==='product');
  const eq=observedEquipment(product?'camera':selected), status=lineStatus();let rows=[],description='실측 관측을 기다리고 있습니다.';
  const metrics=eq?.metrics||{}, fresh=equipmentFresh(eq)&&worldFresh();
  text('selectionHeading',product?product.product_id||product.id:equipmentNames[selected]||selected);
  text('selectionCode',product?'PRODUCT DETAIL':'LINE 01 · EQUIPMENT DETAIL');
  if(product) {
    const result=verdictFor(product), identity=product.product_id||product.id;
    badge('selectionBadge',!worldFresh()?'stale':result?.outcome==='defective'?'warning':result?'normal':'waiting',!worldFresh()?'관측 지연':result?.outcome==='defective'?'불량 판정':result?'결함 미검출':'미검사');
    description='제품 ID '+identity+' · 실제 위치와 검사 결과를 연결합니다.';
    rows=[['공정 단계',stages[product.stage]||product.stage||'관측 대기'],['위치 X',fmt(product.position?.[0],3)+' m'],['실측 속도',Array.isArray(product.velocity)&&product.velocity.every(finite)?fmt(Math.hypot(...product.velocity),3)+' m/s':'—'],['검사 ID',product.inspection_id||'미검사'],['검사 시각',localTime(result?.camera_timestamp||result?.captured_at)],['검사 당시 HI',fmt(result?.health_index)],['모델',result?.model_version||'—']];
    $('monitorLink').href='/monitor?equipment=camera&product='+encodeURIComponent(identity)+'&run='+encodeURIComponent(latest.run_id)+(product.inspection_id?'&inspection='+encodeURIComponent(product.inspection_id):'');
  } else {
    badge('selectionBadge',selected==='conveyor'?status.state:fresh?eq?.state||'waiting':'stale',selected==='conveyor'?status.label:fresh?eq?.status_label||'관측 대기':'관측 지연');
    description=eq?.reason||description;
    if(selected==='motor')rows=[['회전수',fmt(metrics.rpm,0)+' RPM'],['Health Index',fmt(metrics.health_index)],['진동 RMS',fmt(metrics.vibration_rms,3)+' a.u.'],['온도',fmt(metrics.temperature)+' °C'],['진단 시간',fmt(metrics.inference_ms,2)+' ms']];
    else if(selected==='camera')rows=[['마지막 AI 판정',localTime(metrics.last_inspection_at)],['판정 경과',finite(metrics.last_inspection_age_seconds)?fmt(metrics.last_inspection_age_seconds+(performance.now()-snapshotReceived)/1000,0)+'초 전':'판정 대기'],['마지막 검출 영역',finite(metrics.detections)?metrics.detections+'개':'—'],['추론 시간',fmt(metrics.inference_ms,2)+' ms'],['카메라 촬영',localTime(envelope?.cameras?.inspection?.timestamp)]];
    else rows=[['롤러 실측 속도',fmt(lineMeasurements().roller,3)+' rad/s'],['제품 최대 속도',fmt(lineMeasurements().productSpeed,3)+' m/s'],['고장 레벨',fmt(metrics.fault_level,0)+' / 10'],['모터 회전수',fmt(metrics.motor_rpm,0)+' RPM'],['시뮬레이션 시간',fmt(latest?.sim_time,2)+' s']];
    $('monitorLink').href='/monitor?equipment='+encodeURIComponent(selected);
  }
  text('selectionDescription',description);
  const fragment=document.createDocumentFragment();for(const [label,value]of rows){const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;div.append(dt,dd);fragment.append(div);}$('selectionMetrics').replaceChildren(fragment);
}
function renderProducts(force=false) {
  const plates=(latest?.objects||[]).filter(object=>object.kind==='product');
  const signature=plates.map(object=>[object.id,object.product_id,object.stage,object.inspection_id,verdictFor(object)?.outcome,selected===object.id].join(':')).join('|');
  if(signature===lastProductSignature&&!force)return;lastProductSignature=signature;
  if(!plates.length){const p=document.createElement('p');p.className='empty-products';p.textContent='첫 제품 관측을 기다리고 있습니다.';$('productList').replaceChildren(p);return;}
  const fragment=document.createDocumentFragment();for(const object of plates){
    const result=verdictFor(object),button=document.createElement('button');button.type='button';button.className='product-card'+(selected===object.id?' selected':'');button.setAttribute('aria-pressed',String(selected===object.id));
    const title=document.createElement('strong'),small=document.createElement('small'),stage=document.createElement('span'),verdict=document.createElement('span');
    title.textContent=object.product_id||object.id;small.textContent=object.inspection_id?'검사 '+object.inspection_id:'검사 ID 대기';
    stage.className='product-stage';stage.textContent=stages[object.stage]||object.stage||'단계 대기';verdict.className='verdict '+(result?.outcome||'unknown');verdict.textContent=result?.outcome==='defective'?'불량 판정':result?'결함 미검출':'미검사';stage.append(verdict);button.append(title,small,stage);button.addEventListener('click',()=>select(object.id,true));fragment.append(button);
    }$('productList').replaceChildren(fragment);text('trackingCount',plates.length+'개 개별 제품');
}
function renderCameras() {
  for(const name of ['overview','inspection']) {
    const meta=envelope?.cameras?.[name], img=$(name+'Image'), alive=meta&&meta.stale!==true&&finite(meta.age_ms)&&meta.age_ms+Math.max(0,performance.now()-worldReceived)<2500;
    const visibleImage=!img.hidden&&img.naturalWidth>0;
    text(name+'Badge',alive?(visibleImage?'실제 영상 · 라이브':'영상 연결 중'):meta||visibleImage?'마지막 영상 · 관측 지연':'관측 대기');$(name+'Badge').className='camera-live '+(alive&&visibleImage?'fresh':'stale');
    text(name+'Time','촬영 '+localTime(meta?.timestamp));text(name+'Metrics','시뮬레이션 '+fmt(meta?.sim_time,2)+' s'+(finite(meta?.age_ms)?' · '+fmt((meta.age_ms+Math.max(0,performance.now()-worldReceived))/1000,1)+'초 전':''));
    if(alive&&!cameraSources.has(name)) {cameraSources.set(name,true);img.src='/api/camera/'+name+'/stream';}
  }
}
function renderUI() {
  if(disposed)return;
  const fresh=worldFresh(),status=lineStatus(),{roller,plates}=lineMeasurements();
  $('connection').className='connection '+(fresh?'online':'attention');
  $('connection').replaceChildren(document.createElement('i'),document.createTextNode(fresh?(socketOpen?'라이브 연결':'HTTP 관측'):latest?'관측 지연':'연결 대기'));
  text('updatedAt','관측 '+localTime(latest?.timestamp));badge('worldBadge',status.state,fresh?'Gazebo 관측 연결':'관측 지연');
  $('viewport').classList.toggle('is-live',fresh);$('viewport').classList.toggle('is-stale',!fresh&&!!latest);
  text('viewportState',fresh?((latest.scenario_mode==='balanced-evaluation'?'평가 전용 · ':'')+status.label+' · Gazebo 실측'):latest?'마지막 관측 화면 · 움직임 갱신 중지':'첫 관측 대기');
  $('systemNotice').hidden=fresh;text('systemNotice',latest?'세계 상태의 관측이 지연되었습니다. 마지막 관측 위치를 유지하며 제어를 잠급니다. 실제 물리 정지 여부는 새 관측이 필요합니다.':'Gazebo의 첫 관측을 기다리고 있습니다. 서비스가 연결되면 자동으로 갱신합니다.');
  text('lineState',status.label);text('lineDetail','실측 롤러 '+fmt(roller,3)+' rad/s');text('productCount',latest?plates.length:'—');
  const inspection=plates.filter(object=>['inspection','inspecting'].includes(object.stage)).length;text('stageCounts','검사 구역 '+inspection+'개 · 개별 ID 추적');
  const motor=observedEquipment('motor'), motorFresh=equipmentFresh(motor);text('healthValue',fmt(motor?.metrics?.health_index));text('healthDetail',motorFresh?(motor?.status_label+' · 관측 '+localTime(motor.observed_at)):'HI · 마지막 관측 확인');
  const counts=snapshot?.counts||{};text('qualityRate',finite(counts.inspections)&&counts.inspections>0&&finite(counts.defects)?fmt(counts.defects/counts.inspections*100):'—');text('qualityDetail',finite(counts.inspections)?counts.inspections+'개 검사 · '+counts.defects+'개 불량':'검사 결과 대기');
  const now=performance.now(),windowSamples=observations.filter(at=>now-at<2500);const hz=windowSamples.length>1?(windowSamples.length-1)*1000/(windowSamples.at(-1)-windowSamples[0]):null;text('worldHz','관측 '+(fresh?fmt(hz,1):'—')+' Hz');
  const firstSample=samples[0],lastSample=samples.at(-1),rtf=firstSample&&lastSample&&lastSample.at>firstSample.at?(lastSample.state.sim_time-firstSample.state.sim_time)*1000/(lastSample.at-firstSample.at):null;
  text('realtimeFactor','시뮬레이션 ×'+(fresh?fmt(rtf,2):'—'));
  const lag=fresh&&finite(displayedDelay)?displayedDelay:finite(envelope?.age_ms)?envelope.age_ms+Math.max(0,now-worldReceived):null;
  const sorted=latencySamples.slice().sort((a,b)=>a-b),p95=sorted.length?sorted[Math.min(sorted.length-1,Math.floor(sorted.length*.95))]:null;
  text('displayDelay','관측 지연 '+fmt(lag,0)+' ms'+(fresh&&p95!==null?' · p95 '+fmt(p95,0):''));
  text('worldMetadata',latest?'world '+latest.world_version+' · run '+latest.run_id+' · seq '+latest.sequence:'Gazebo 관측 기반 · 실제 카메라 영상');
  renderSelection();renderProducts();renderCameras();updateControls();
  // Canvas metadata exposes the same read-only drawing telemetry to isolated QA tools.
  if(typeof window.factoryDiagnostics==='function')$('worldCanvas').dataset.diagnostics=JSON.stringify(window.factoryDiagnostics());
}
function updateControls() {
  const currentEpoch=!!latest&&snapshot?.world?.state?.run_id===latest.run_id;
  const allowed=currentEpoch&&worldFresh()&&envelope?.mqtt_connected===true&&snapshot?.process?.mqtt_connected===true;
  $('stop').disabled=!allowed||busy||!!pending||latest?.running===false;
  const resetReady=allowed&&equipmentFresh(observedEquipment('motor'))&&equipmentFresh(observedEquipment('conveyor'))&&snapshot?.process?.reset_ready===true;
  $('restart').disabled=!resetReady||busy||!!pending;
  text('controlLock',allowed?'관측 연결':'제어 잠금');text('resetHint',allowed?snapshot?.process?.reset_reason||'고장 레벨 0 · HI 80 이상에서 수동 재시작됩니다.':'최근 센서·라인·세계 관측과 MQTT 연결이 필요합니다.');
}
function observeCommand() {
  if(!pending)return;
  if(worldFresh()&&latest.run_id===pending.run&&latest.sequence>pending.sequence) {
    const {roller,productSpeed}=lineMeasurements();
    if(pending.kind==='stop'&&latest.running===false&&finite(roller)&&roller<=.01) {
      if(finite(productSpeed)&&productSpeed>.03)text('commandNotice','롤러 정지 관측 · 제품 잔류 이동을 확인하고 있습니다.');
      else {text('commandNotice','정지 확인 · 실제 롤러와 제품의 속도가 낮아졌습니다.');pending=null;}
    }else if(pending.kind==='reset'&&latest.running===true&&finite(roller)&&roller>.01){text('commandNotice','재시작 확인 · 실제 롤러 가동을 관측했습니다.');pending=null;}
  }
  if(pending&&performance.now()-pending.at>15000){text('commandNotice','요청 접수 후 15초 안에 적용을 확인하지 못했습니다. 관제의 현재 상태와 알람을 확인하세요.');pending=null;}
  updateControls();
}
async function command(kind) {
  const button=$(kind==='stop'?'stop':'restart');if(button.disabled)return;
  busy=true;updateControls();$('commandNotice').classList.remove('error');text('commandNotice',kind==='stop'?'정지 요청 전송 중…':'안전 재시작 요청 전송 중…');
  const controller=new AbortController();requestControllers.add(controller);const timeout=setTimeout(()=>controller.abort(),6500);
  const before={run:latest.run_id,sequence:latest.sequence};
  try {
    const response=await fetch('/api/'+kind,{method:'POST',signal:controller.signal}),data=await response.json();
    if(!response.ok||data.accepted!==true)throw new Error(typeof data.detail==='string'?data.detail:'명령 요청 실패 · HTTP '+response.status);
    pending={kind,...before,at:performance.now()};text('commandNotice',kind==='stop'?'정지 요청 접수 · 실제 감속·정지를 확인 중입니다.':'재시작 요청 접수 · 실제 롤러 가동을 기다립니다.');
  }catch(error){$('commandNotice').classList.add('error');text('commandNotice',error.name==='AbortError'?'응답 지연으로 요청 접수 여부가 불확실합니다. 관제 상태를 먼저 확인하세요.':error.message);}
  finally{clearTimeout(timeout);requestControllers.delete(controller);busy=false;updateControls();}
}
function pause() {
  paused=true;clearTimeout(reconnectTimer);clearTimeout(worldTimer);clearTimeout(snapshotTimer);clearTimeout(productTimer);clearInterval(uiTimer);cancelAnimationFrame(animationId);
  if(socket){const active=socket;socket=null;active.close();}socketOpen=false;
  for(const controller of requestControllers)controller.abort();
  for(const name of cameraSources.keys())$(name+'Image').removeAttribute('src');cameraSources.clear();
}
function resume() {
  if(disposed)return;paused=false;worldReceived=-Infinity;samples=[];connectWorld();pollWorld();pollSnapshot();pollProducts();uiTimer=setInterval(()=>{renderUI();observeCommand();},500);
  if(renderer){fpsStarted=performance.now();frames=0;animationId=requestAnimationFrame(animate);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
document.querySelectorAll('[data-select]').forEach(button=>button.addEventListener('click',()=>select(button.dataset.select,true)));
$('follow').addEventListener('click',()=>{follow=!follow&&meshes.has(selected);$('follow').setAttribute('aria-pressed',String(follow));});
$('stop').addEventListener('click',()=>command('stop'));$('restart').addEventListener('click',()=>command('reset'));
for(const name of ['overview','inspection']) {
  const img=$(name+'Image');
  img.addEventListener('load',()=>{img.hidden=false;$(name+'Empty').hidden=true;});
  img.addEventListener('error',()=>{cameraSources.delete(name);img.hidden=true;$(name+'Empty').hidden=false;text(name+'Empty','실제 카메라 영상을 기다리고 있습니다.');});
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();else resume();});
window.addEventListener('pagehide',event=>{pause();if(!event.persisted){disposed=true;resizeObserver?.disconnect();controls?.dispose();disposeWorld();renderer?.dispose();}});
window.addEventListener('pageshow',event=>{if(event.persisted)resume();});
const mobileLayout=matchMedia('(max-width:680px)');
$('selectionDetails').open=!mobileLayout.matches||query.has('equipment');
mobileLayout.addEventListener('change',event=>{$('selectionDetails').open=!event.matches;});
select(selected);resume();initViewer();
// Read-only, bounded telemetry for browser QA. It cannot modify the scene or control the line.
window.factoryDiagnostics=()=>({displayed_seq:displayedSequence,displayed_run_id:displayedRun,latest_seq:latest?.sequence,
  latestUTC:latest?.timestamp,displayedUTC:displayedAt,fps,observed_to_display_ms:latencySamples.slice(),
  observed_to_display_p95_ms:latencySamples.length?latencySamples.slice().sort((a,b)=>a-b)[Math.min(latencySamples.length-1,Math.floor(latencySamples.length*.95))]:null,
  buffer_length:samples.length,connected:worldFresh(),stale:!worldFresh(),world_version:layout?.world_version,
  product_ids:(latest?.objects||[]).filter(object=>object.kind==='product').map(object=>object.product_id||object.id),
  observed_positions:(latest?.objects||[]).filter(object=>object.kind==='product').map(object=>({id:object.product_id||object.id,position:object.position})),
  displayed_positions:[...meshes.values()].filter(object=>object.userData.kind==='product'&&object.visible).map(object=>({id:object.userData.renderedObservation?.product_id||object.userData.id,slot_id:object.userData.id,run_id:object.userData.renderedRun,inspection_id:object.userData.renderedObservation?.inspection_id||null,position:object.position.toArray()})),
  camera_metadata:structuredClone(envelope?.cameras||{}),selected,renderer_available:!!renderer&&!rendererLost,quality_reduced:qualityReduced,scenario_mode:latest?.scenario_mode});
