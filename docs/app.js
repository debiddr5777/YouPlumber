/* YouPlumber Standalone — Browser-based YouTube Audio Downloader */

var PIPED_INSTANCES = [
  'https://pipedapi.kavin.rocks',
  'https://pipedapi.r4fo.com',
  'https://pipedapi.adminforge.de'
];
var currentInstance = 0;
var searchResults = [];
var downloadQueue = [];
var queueIdCounter = 0;

function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function durF(s){if(!s)return '—';var m=Math.floor(s/60),sec=s%60;return m+':'+(sec<10?'0':'')+sec;}
function viewsF(n){if(!n)return '';if(n>=1e6)return (n/1e6).toFixed(1)+'M views';if(n>=1e3)return (n/1e3).toFixed(1)+'K views';return n+' views';}
function sizeF(b){if(!b)return '';return b>=1e6?(b/1e6).toFixed(1)+' MB':b>=1e3?(b/1e3).toFixed(0)+' KB':b+' B';}

function toast(msg,type){
  type=type||'ok';var el=document.createElement('div');
  el.className='toast toast-'+type;el.textContent=msg;
  document.body.appendChild(el);
  setTimeout(function(){el.style.opacity='0';el.style.transition='opacity .3s';setTimeout(function(){el.remove()},300)},3500);
}

async function pipedFetch(path){
  for(var i=0;i<PIPED_INSTANCES.length;i++){
    var idx=(currentInstance+i)%PIPED_INSTANCES.length;
    try{
      var r=await fetch(PIPED_INSTANCES[idx]+path);
      if(r.ok){currentInstance=idx;return await r.json();}
    }catch(_){}
  }
  throw new Error('All API instances failed. Try again later.');
}

/* ===== Search ===== */
async function doSearch(){
  var q=document.getElementById('search-input').value.trim();
  if(!q){toast('Type a search query','warn');return;}
  var btn=document.getElementById('btn-search');
  btn.disabled=true;btn.innerHTML='<i class="fas fa-spinner fa-spin"></i> Searching…';
  document.getElementById('status-text').textContent='Searching…';
  document.getElementById('status-dot').className='dot dot-busy';
  try{
    var data=await pipedFetch('/search?q='+encodeURIComponent(q)+'&filter=videos');
    searchResults=(data.items||data).filter(function(i){return i.type==='stream'||i.url;}).slice(0,20);
    renderResults(q);
  }catch(e){toast(e.message,'err');}
  btn.disabled=false;btn.innerHTML='<i class="fas fa-magnifying-glass"></i> Search';
  document.getElementById('status-text').textContent='Idle';
  document.getElementById('status-dot').className='dot dot-idle';
}

function renderResults(query){
  var panel=document.getElementById('results');panel.style.display='';
  document.getElementById('res-label').textContent='Results for "'+query+'"';
  document.getElementById('res-count').textContent=searchResults.length+' found';
  var list=document.getElementById('res-list');
  if(!searchResults.length){list.innerHTML='<div class="empty"><p>No results found</p></div>';return;}
  list.innerHTML=searchResults.map(function(item,i){
    var vid=(item.url||'').replace('/watch?v=','');
    var dur=durF(item.duration);
    var thumb=item.thumbnail||'';
    var title=item.title||'Untitled';
    var channel=item.uploaderName||item.uploader||'';
    var views=viewsF(item.views);
    return '<div class="result-row">'+
      '<input type="checkbox" class="r-check res-cb" data-idx="'+i+'" checked onchange="countSel()">'+
      (thumb?'<img src="'+esc(thumb)+'" class="r-thumb" loading="lazy" onerror="this.style.display=\'none\'">':'')+
      '<div class="r-info"><div class="r-title">'+esc(title)+'</div><div class="r-meta">'+esc(channel)+(views?' · '+views:'')+(dur?' · '+dur:'')+'</div></div>'+
      '<button class="btn-play-preview" title="Play Preview" onclick="event.stopPropagation();playPreview(\''+vid+'\', \''+esc(title).replace(/'/g,"\\'")+'\', \''+esc(channel).replace(/'/g,"\\'")+'\', \''+esc(thumb).replace(/'/g,"\\'")+'\')"><i class="fas fa-play"></i></button>'+
      '<button class="btn-dl" onclick="downloadSingle('+i+')"><i class="fas fa-arrow-down"></i> Download</button></div>';
  }).join('');
  countSel();
}

function countSel(){
  var n=document.querySelectorAll('.res-cb:checked').length;
  document.getElementById('sel-count').textContent=n+' selected';
}
function toggleAll(){
  var c=document.getElementById('sel-all').checked;
  document.querySelectorAll('.res-cb').forEach(function(cb){cb.checked=c;});
  countSel();
}

/* ===== Download Single ===== */
async function downloadSingle(idx){
  var item=searchResults[idx];if(!item)return;
  var vid=(item.url||'').replace('/watch?v=','');
  var title=item.title||'audio';
  addToDownloadQueue(vid,title);
}

/* ===== Download Selected ===== */
function downloadSelected(){
  var cbs=document.querySelectorAll('.res-cb:checked');
  if(!cbs.length){toast('Select tracks first','warn');return;}
  cbs.forEach(function(cb){
    var idx=parseInt(cb.dataset.idx);
    var item=searchResults[idx];
    if(item){
      var vid=(item.url||'').replace('/watch?v=','');
      addToDownloadQueue(vid,item.title||'audio');
    }
  });
  toast('Added '+cbs.length+' to download queue','ok');
}

/* ===== Download Queue ===== */
function addToDownloadQueue(videoId,title){
  var id=++queueIdCounter;
  var entry={id:id,videoId:videoId,title:title,status:'waiting',progress:0,error:null};
  downloadQueue.push(entry);
  renderQueue();
  processDownload(entry);
}

function renderQueue(){
  var section=document.getElementById('dl-section');
  var list=document.getElementById('dl-list');
  section.style.display=downloadQueue.length?'':'none';
  document.getElementById('dl-count').textContent=downloadQueue.length;
  list.innerHTML=downloadQueue.map(function(e){
    var tagClass=e.status==='done'?'tag-done':e.status==='downloading'?'tag-dl':e.status==='error'?'tag-err':'tag-wait';
    var tagText=e.status==='done'?'DONE':e.status==='downloading'?'DOWNLOADING':e.status==='error'?'FAILED':'WAITING';
    var prog=e.status==='downloading'?'<div class="prog-track"><div class="prog-fill" style="width:'+e.progress+'%"></div></div>':'';
    return '<div class="dl-item"><div class="dl-icon"><i class="fas fa-'+(e.status==='done'?'check':e.status==='downloading'?'spinner fa-spin':e.status==='error'?'xmark':'clock')+'"></i></div>'+
      '<div class="dl-info"><div class="dl-title">'+esc(e.title)+'</div><div class="dl-meta">'+(e.error||e.status)+'</div>'+prog+'</div>'+
      '<span class="tag '+tagClass+'">'+tagText+'</span></div>';
  }).join('');
}

async function processDownload(entry){
  entry.status='downloading';entry.progress=10;renderQueue();
  try{
    var data=await pipedFetch('/streams/'+entry.videoId);
    entry.progress=30;renderQueue();
    var audioStreams=(data.audioStreams||[]).filter(function(s){return s.mimeType&&s.mimeType.indexOf('audio')===0;});
    if(!audioStreams.length)throw new Error('No audio streams found');
    // Pick best quality audio
    audioStreams.sort(function(a,b){return (b.bitrate||0)-(a.bitrate||0);});
    var best=audioStreams[0];
    entry.progress=50;renderQueue();
    // Fetch the audio blob
    var response=await fetch(best.url);
    if(!response.ok)throw new Error('Stream fetch failed ('+response.status+')');
    var blob=await response.blob();
    entry.progress=90;renderQueue();
    // Determine extension from mime type
    var ext='.m4a';
    if(best.mimeType.indexOf('webm')>=0)ext='.webm';
    else if(best.mimeType.indexOf('opus')>=0)ext='.opus';
    else if(best.mimeType.indexOf('mp4')>=0)ext='.m4a';
    else if(best.mimeType.indexOf('mpeg')>=0)ext='.mp3';
    // Trigger browser download dialog
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;
    a.download=sanitizeFilename(entry.title)+ext;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(url)},5000);
    entry.status='done';entry.progress=100;
    toast('Downloaded: '+entry.title,'ok');
  }catch(e){
    entry.status='error';entry.error=e.message;
    toast('Failed: '+e.message,'err');
  }
  renderQueue();
}

function sanitizeFilename(name){
  return (name||'audio').replace(/[\\/:*?"<>|]+/g,'').replace(/\s+/g,' ').trim().slice(0,200)||'audio';
}

/* ===== Audio Player ===== */
var audioEl = document.getElementById('html-audio');

async function playPreview(vid, title, artist, thumb){
  var player = document.getElementById('audio-player');
  player.style.display = 'flex';
  document.getElementById('player-title').textContent = title || 'Loading...';
  document.getElementById('player-meta').textContent = artist || 'Fetching stream...';
  var tImg = document.getElementById('player-thumb');
  if(thumb){ tImg.src = thumb; tImg.style.display = ''; } else { tImg.style.display = 'none'; }
  document.getElementById('play-icon').className = 'fas fa-spinner fa-spin';
  audioEl.pause();
  
  try {
    var data = await pipedFetch('/streams/'+vid);
    var audioStreams = (data.audioStreams||[]).filter(function(s){return s.mimeType&&s.mimeType.indexOf('audio')===0;});
    if(!audioStreams.length) throw new Error('No audio found');
    audioStreams.sort(function(a,b){return (b.bitrate||0)-(a.bitrate||0);});
    audioEl.src = audioStreams[0].url;
    audioEl.play();
    document.getElementById('player-meta').textContent = artist || 'Playing';
    document.getElementById('play-icon').className = 'fas fa-pause';
  } catch(e) {
    document.getElementById('player-meta').textContent = 'Error: ' + e.message;
    document.getElementById('play-icon').className = 'fas fa-play';
    toast(e.message, 'err');
  }
}

function togglePlay(){
  if(audioEl.paused){ if(audioEl.src) audioEl.play(); document.getElementById('play-icon').className='fas fa-pause'; }
  else{ audioEl.pause(); document.getElementById('play-icon').className='fas fa-play'; }
}

function closePlayer(){
  audioEl.pause();
  document.getElementById('audio-player').style.display='none';
}

function seekAudio(val){
  if(!audioEl.duration) return;
  audioEl.currentTime = (val/100) * audioEl.duration;
}

audioEl.addEventListener('timeupdate', function(){
  if(!audioEl.duration) return;
  document.getElementById('player-time').textContent = durF(Math.floor(audioEl.currentTime));
  document.getElementById('player-dur').textContent = durF(Math.floor(audioEl.duration));
  document.getElementById('player-seek').value = (audioEl.currentTime / audioEl.duration) * 100;
});
audioEl.addEventListener('ended', function(){ document.getElementById('play-icon').className='fas fa-play'; });

/* ===== Init ===== */
document.getElementById('search-input').addEventListener('keydown',function(e){if(e.key==='Enter')doSearch();});
