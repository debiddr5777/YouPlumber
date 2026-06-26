/* ===== YouPlumber — App Logic ===== */
var S={running:false,stats:{ok:0,failed:0,total:0,done:0,bytes:0},progress:{},recent:[],queuedTracks:[],queuedIds:new Set(),searchResults:[]};
var ws,refTimer,sourceId=null,searchMode='url';
var _activeEls={},_queueEls={},_recentMap={};

function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function sizeF(b){return b>=1e9?(b/1e9).toFixed(2)+' GB':b>=1e6?(b/1e6).toFixed(1)+' MB':b>=1e3?(b/1e3).toFixed(0)+' KB':b+' B';}
function durF(d){if(!d)return '—';var m=Math.floor(d/60),s=d%60;return m+':'+(s<10?'0':'')+s;}

async function api(path,o){
  o=o||{};var r=await fetch(path,{headers:{'Content-Type':'application/json'},method:o.method||'GET',body:o.body||null});
  if(!r.ok){var e;try{e=(await r.json()).detail}catch(_){e=r.statusText}throw new Error(e||r.statusText)}
  return r.json();
}

function toast(m,t){
  t=t||'info';var el=document.createElement('div');
  el.className='toast toast-'+t;el.textContent=m;
  document.body.appendChild(el);
  setTimeout(function(){el.style.opacity='0';el.style.transition='opacity .3s';setTimeout(function(){el.remove()},300)},3500);
}

/* ===== Tab Switching ===== */
function setSearchMode(mode){
  searchMode=mode;
  document.querySelectorAll('.search-tab').forEach(function(t){t.classList.toggle('active',t.dataset.mode===mode)});
  var inp=document.getElementById('main-input');
  if(mode==='search'){inp.placeholder='Search YouTube — e.g. "afro house 2026"';inp.parentNode.querySelector('i').className='fas fa-magnifying-glass';}
  else{inp.placeholder='Paste a YouTube URL (channel, playlist, or video)';inp.parentNode.querySelector('i').className='fas fa-link';}
  inp.focus();
}

/* ===== WebSocket ===== */
function connectWS(){
  var p=location.protocol==='https:'?'wss:':'ws:';
  ws=new WebSocket(p+'//'+location.host+'/ws/progress');
  ws.onmessage=function(e){try{var d=JSON.parse(e.data);S.running=d.running;for(var k in d.stats)S.stats[k]=d.stats[k];S.progress=d.progress||{};syncAll();}catch(_){}};
  ws.onclose=function(){setTimeout(connectWS,2000);};
}

/* ===== Polling ===== */
function startRefresh(){
  refTimer=setInterval(function(){
    api('/api/tracks/recent?limit=10').then(function(r){S.recent=r;syncRecent();}).catch(function(){});
    api('/api/stats').then(function(s){for(var k in s)S.stats[k]=s[k];syncHeader();syncQueue();}).catch(function(){});
    api('/api/tracks?status=queued&limit=500').then(function(t){S.queuedTracks=t;syncQueueList();}).catch(function(){});
    api('/api/tracks?status=failed&limit=50').then(function(t){if(t.length){S.queuedTracks=(S.queuedTracks||[]).concat(t);syncQueueList();}}).catch(function(){});
    api('/api/tracks?status=downloading&limit=50').then(function(t){if(t.length&&!Object.keys(S.progress).length){t.forEach(function(x){if(!S.progress[x.id])S.progress[x.id]={title:x.title,pct:0}});syncActive();}}).catch(function(){});
    if(!S.running&&Object.keys(S.progress).length){S.progress={};syncActive();}
  },2000);
}

function syncAll(){syncHeader();syncActive();syncQueue();syncQueueList();syncRecent();}

/* ===== Header ===== */
function syncHeader(){
  var s=S.stats,act=Object.keys(S.progress).length;
  document.getElementById('head-queued').textContent=(s.queued||0);
  document.getElementById('head-active').textContent=act||'0';
  document.getElementById('head-done').textContent=s.done||0;
  document.getElementById('head-disk').textContent=sizeF(s.bytes||0);
  var dot=document.getElementById('live-dot');
  dot.className='stat-dot'+(act||S.running?' active':'');
  document.getElementById('live-label').textContent=(act||S.running)?act+' active':'Idle';
}

/* ===== Active Downloads ===== */
function syncActive(){
  var area=document.getElementById('active-box'),entries=Object.entries(S.progress);
  var incoming=new Set(entries.map(function(e){return e[0];}));
  for(var tid in _activeEls){
    if(!incoming.has(tid)){_activeEls[tid].remove();delete _activeEls[tid];}
  }
  entries.forEach(function(e){
    var tid=e[0],p=e[1],el=_activeEls[tid];
    if(!el){
      el=document.createElement('div');el.className='active-item';
      el.innerHTML='<div class="active-header"><span class="active-title"></span><span class="active-pct"></span></div><div class="progress-track"><div class="progress-fill"></div></div><div class="active-stats"><span class="dl-speed"></span><span class="dl-eta"></span></div>';
      area.appendChild(el);_activeEls[tid]=el;
    }
    var pct=Math.round(p.pct||0);
    el.querySelector('.active-title').textContent=(p.title||'').slice(0,50);
    el.querySelector('.active-pct').textContent=pct+'%';
    el.querySelector('.progress-fill').style.width=pct+'%';
    el.querySelector('.dl-speed').textContent=p.speed?(p.speed/1024/1024).toFixed(1)+' MB/s':'';
    el.querySelector('.dl-eta').textContent=p.eta?Math.round(p.eta)+'s left':'';
  });
  if(!entries.length&&S.running&&!area.children.length){
    area.innerHTML='<div class="empty-state" style="padding:20px"><i class="fas fa-spinner fa-spin"></i><p>Starting…</p></div>';
  }else if(!entries.length&&!S.running){area.innerHTML='';}
}

/* ===== Queue ===== */
function syncQueue(){
  document.getElementById('queue-badge').textContent=(S.stats.queued||0)+(Object.keys(S.progress).length||0);
  var go=document.getElementById('btn-go'),halt=document.getElementById('btn-halt');
  if(S.running){go.style.display='none';halt.style.display='';}else{go.style.display='';halt.style.display='none';}
  var has=(S.queuedTracks&&S.queuedTracks.length>0)||Object.keys(S.progress).length>0;
  document.getElementById('q-empty').style.display=has?'none':'';
}

function syncQueueList(){
  var list=document.getElementById('q-list'),tracks=S.queuedTracks||[];
  if(!tracks.length){list.innerHTML='';for(var k in _queueEls)delete _queueEls[k];syncQueue();return;}
  var incoming=new Set();
  tracks.forEach(function(t){
    var tid=String(t.id);incoming.add(tid);var el=_queueEls[tid];
    if(!el){
      el=document.createElement('div');el.className='queue-item';
      el.innerHTML='<div class="queue-info"><div class="queue-title"></div><div class="queue-meta"></div></div><span class="tag"></span>';
      list.appendChild(el);_queueEls[tid]=el;
    }
    el.querySelector('.queue-title').textContent=(t.title||'').slice(0,50);
    el.querySelector('.queue-meta').textContent=(t.uploader||'')+' · '+durF(t.duration);
    var tag=el.querySelector('.tag'),prog=S.progress[t.id];
    if(prog){tag.className='tag tag-active';tag.textContent=Math.round(prog.pct||0)+'%';}
    else if(t.status==='failed'){tag.className='tag tag-failed';tag.textContent='FAILED';}
    else if(t.status==='done'){tag.className='tag tag-done';tag.textContent='DONE';}
    else{tag.className='tag tag-queued';tag.textContent='QUEUED';}
  });
  for(var tid in _queueEls){if(!incoming.has(tid)){_queueEls[tid].remove();delete _queueEls[tid];}}
  syncQueue();
}

/* ===== Recent ===== */
function syncRecent(){
  var r=S.recent;
  document.getElementById('recent-badge').textContent=r.length;
  document.getElementById('recent-empty').style.display=r.length?'none':'';
  var list=document.getElementById('recent-list'),incoming=new Set();
  r.forEach(function(t){
    var tid=String(t.id);incoming.add(tid);var el=_recentMap[tid];
    if(!el){
      el=document.createElement('div');el.className='recent-item';
      el.innerHTML='<div class="recent-icon"><i class="fas fa-music"></i></div><div class="recent-info"><div class="recent-title"></div><div class="recent-meta"></div><div class="recent-file" style="display:none"><i class="fas fa-folder-open" style="margin-right:3px"></i><span class="fn"></span></div></div><span class="tag tag-done">DONE</span>';
      list.appendChild(el);_recentMap[tid]=el;
    }
    el.querySelector('.recent-title').textContent=(t.title||'').slice(0,40);
    el.querySelector('.recent-meta').textContent=(t.uploader||'')+' · '+durF(t.duration);
    var fp=t.file_path||'',fn=fp.replace(/\\/g,'/').split('/').pop()||'';
    var fl=el.querySelector('.recent-file');
    if(fp){fl.style.display='';fl.querySelector('.fn').textContent=fn;fl.onclick=function(){openFile(t.id);};}
  });
  for(var tid in _recentMap){if(!incoming.has(tid)){_recentMap[tid].remove();delete _recentMap[tid];}}
}

/* ===== Main Action (Fetch URL or Search) ===== */
async function mainAction(){
  var val=document.getElementById('main-input').value.trim();
  if(!val){toast('Enter a URL or search query','warning');return;}
  if(searchMode==='search'){doSearch(val);}else{fetchURL(val);}
}

async function fetchURL(url){
  var b=document.getElementById('btn-action');b.disabled=true;b.innerHTML='<i class="fas fa-spinner fa-spin"></i> Fetching…';
  try{
    var r=await api('/api/sources',{method:'POST',body:JSON.stringify({url:url,limit:100,name:null})});
    sourceId=r.source_id;
    document.getElementById('result-label').textContent=r.name||'Results';
    document.getElementById('result-count').textContent=r.tracks_added+' track'+(r.tracks_added===1?'':'s');
    var tracks=await api('/api/tracks?source_id='+sourceId+'&limit=200');
    renderResults(tracks,false);
    document.getElementById('main-input').value='';
  }catch(e){toast(e.message,'error');}
  b.disabled=false;b.innerHTML='<i class="fas fa-bolt"></i> Go';
}

async function doSearch(query){
  var b=document.getElementById('btn-action');b.disabled=true;b.innerHTML='<i class="fas fa-spinner fa-spin"></i> Searching…';
  try{
    var results=await api('/api/search',{method:'POST',body:JSON.stringify({query:query,limit:20})});
    S.searchResults=results;
    document.getElementById('result-label').textContent='Search: '+query;
    document.getElementById('result-count').textContent=results.length+' result'+(results.length===1?'':'s');
    renderResults(results,true);
  }catch(e){toast(e.message,'error');}
  b.disabled=false;b.innerHTML='<i class="fas fa-bolt"></i> Go';
}

function renderResults(tracks,isSearch){
  var panel=document.getElementById('results-panel');panel.style.display='';
  var list=document.getElementById('results-list');
  document.getElementById('btn-add-q').dataset.search=isSearch?'1':'0';
  if(!tracks.length){list.innerHTML='<div class="empty-state"><p>No results found</p></div>';return;}
  list.innerHTML=tracks.map(function(t,i){
    var vid=isSearch?t.video_id:t.video_id||t.id;
    var dur=durF(t.duration);
    var thumb=t.thumbnail||'';
    return '<div class="result-row" onclick="var c=this.querySelector(\'input\');c.checked=!c.checked;countSel()">'+
      '<input type="checkbox" class="result-checkbox res-cb" data-idx="'+i+'" data-id="'+(t.id||'')+'" data-vid="'+(vid||'')+'" checked onclick="event.stopPropagation();countSel()">'+
      (thumb?'<img src="'+esc(thumb)+'" class="result-thumb" loading="lazy" onerror="this.style.display=\'none\'">':'')+
      '<div class="result-info"><div class="result-title">'+esc(t.title||'Untitled')+'</div><div class="result-meta">'+esc(t.uploader||t.channel||'')+'&nbsp;·&nbsp;'+dur+'</div></div>'+
      '<span class="result-status">'+(t.status||'new')+'</span></div>';
  }).join('');
  countSel();
}

function toggleAll(){var cb=document.getElementById('sel-all');document.querySelectorAll('.res-cb').forEach(function(c){c.checked=cb.checked});countSel();}
function countSel(){var n=document.querySelectorAll('.res-cb:checked').length;document.getElementById('sel-count').textContent=n+' selected';}
function closeResults(){document.getElementById('results-panel').style.display='none';}

async function addToQueue(){
  var isSearch=document.getElementById('btn-add-q').dataset.search==='1';
  var cbs=document.querySelectorAll('.res-cb:checked');
  if(!cbs.length){toast('Select tracks first','warning');return;}

  if(isSearch){
    var tracks=[];
    cbs.forEach(function(c){var idx=parseInt(c.dataset.idx);if(S.searchResults[idx])tracks.push(S.searchResults[idx]);});
    try{
      var r=await api('/api/search/add',{method:'POST',body:JSON.stringify({tracks:tracks,query:document.getElementById('result-label').textContent.replace('Search: ',''),auto_queue:false})});
      toast('Added '+r.added+' tracks','success');
      // Now queue them
      if(r.source_id){
        var newTracks=await api('/api/tracks?source_id='+r.source_id+'&status=new&limit=500');
        var ids=newTracks.map(function(t){return t.id;});
        if(ids.length)await api('/api/tracks/queue',{method:'POST',body:JSON.stringify({track_ids:ids})});
        toast('Queued '+ids.length+' tracks','success');
      }
    }catch(e){toast(e.message,'error');}
  }else{
    var ids=[];cbs.forEach(function(c){if(c.dataset.id)ids.push(parseInt(c.dataset.id));});
    if(!ids.length){toast('No track IDs found','warning');return;}
    try{
      await api('/api/tracks/queue',{method:'POST',body:JSON.stringify({track_ids:ids})});
      toast('Queued '+ids.length+' tracks','success');
    }catch(e){toast(e.message,'error');}
  }
  cbs.forEach(function(c){c.checked=false;c.disabled=true;c.closest('.result-row').style.opacity='.4';});
  countSel();
  api('/api/tracks?status=queued&limit=500').then(function(t){S.queuedTracks=t;syncQueueList();}).catch(function(){});
}

/* ===== Queue Controls ===== */
async function startQ(){try{var r=await api('/api/download/start',{method:'POST'});toast('Starting '+r.queued+' tracks','success');}catch(e){if(e.message.includes('No tracks'))toast('Queue empty','warning');else toast(e.message,'error');}}
async function stopQ(){await api('/api/download/stop',{method:'POST'});toast('Downloads stopped','warning');}
async function clearQ(){try{await api('/api/tracks/queue',{method:'POST',body:JSON.stringify({reset:true})});S.queuedIds.clear();S.queuedTracks=[];syncQueueList();toast('Queue cleared','warning');}catch(_){}}
async function openFile(id){try{await api('/api/tracks/'+id+'/open');}catch(e){toast(e.message,'error');}}

/* ===== Settings ===== */
async function showSettings(){
  document.getElementById('settings-modal').classList.add('visible');
  try{var c=await api('/api/config');document.getElementById('set-out').value=c.downloads.output_dir||'';document.getElementById('set-codec').value=c.audio.codec||'mp3';document.getElementById('set-bitrate').value=c.audio.mp3_bitrate||'320';document.getElementById('set-jobs').value=c.downloads.concurrent_jobs||4;}catch(_){}
}
function hideSettings(){document.getElementById('settings-modal').classList.remove('visible');}
document.addEventListener('keydown',function(e){if(e.key==='Escape')hideSettings();});
async function saveSettings(){
  var u=[['downloads.output_dir',document.getElementById('set-out').value],['audio.codec',document.getElementById('set-codec').value],['audio.mp3_bitrate',document.getElementById('set-bitrate').value],['downloads.concurrent_jobs',parseInt(document.getElementById('set-jobs').value)]];
  for(var i=0;i<u.length;i++)await api('/api/config',{method:'POST',body:JSON.stringify({key:u[i][0],value:u[i][1]})});
  toast('Settings saved','success');hideSettings();document.getElementById('out-dir').textContent=document.getElementById('set-out').value;
}

/* ===== Sessions ===== */
var SESS_VIS=false;
function toggleSessions(){
  SESS_VIS=!SESS_VIS;
  document.getElementById('sess-list').style.display=SESS_VIS?'':'none';
  document.getElementById('sess-arrow').className='fas fa-chevron-'+(SESS_VIS?'up':'down');
  if(SESS_VIS)fetchSessions();
}
async function fetchSessions(){
  try{
    var ss=await api('/api/sessions');
    document.getElementById('sess-badge').textContent=ss.length;
    document.getElementById('sess-list').innerHTML=ss.slice(0,10).map(function(s){
      var start=new Date(s.started_at*1000).toLocaleString();
      return '<div class="sess-item"><div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--text-secondary)">#'+s.id+'</span><span style="color:'+(s.ended_at?'var(--text-tertiary)':'var(--green)')+'">'+(s.ended_at?'Done':'Active')+'</span></div><div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">'+start+'</div><div style="font-size:11px;color:var(--text-tertiary)">✓ '+s.tracks_ok+' tracks'+(s.tracks_failed?' · ✗ '+s.tracks_failed:'')+'</div></div>';
    }).join('');
  }catch(_){}
}

/* ===== Init ===== */
connectWS();startRefresh();
api('/api/config').then(function(c){document.getElementById('out-dir').textContent=c.downloads.output_dir||''}).catch(function(){});
setInterval(function(){if(SESS_VIS)fetchSessions();},10000);
