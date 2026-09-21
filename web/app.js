'use strict';
(() => {
  const paths = {
    garage:'M3 10 12 3l9 7v11H3V10Zm4 11v-9h10v9M7 16h10',
    search:'m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
    user:'M20 21v-2a7 7 0 0 0-14 0v2M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
    heart:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
    car:'m5 8 2-5h10l2 5M3 9l2-1h14l2 1v9H3V9Zm2 9v3m14-3v3M6 12h2m8 0h2M8 16h8',
    plus:'M12 5v14M5 12h14', arrow:'M7 17 17 7M7 7h10v10', back:'m14 6-6 6 6 6M8 12h13',
    lock:'M6 10h12v11H6V10Zm2 0V6a4 4 0 0 1 8 0v4',
    phone:'M5 3H2v4c0 8 7 15 15 15h4v-5l-5-2-2 3a14 14 0 0 1-8-8l3-2-2-5H5Z',
    pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    shield:'M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4ZM8 8l8 8m0-8-8 8',
    refresh:'M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 14-3l1 7M4 12l1 7a8 8 0 0 0 14-3'
  };
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.car}"/></svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sources = {encar:'엔카',kcar:'케이카',kbchachacha:'KB차차차',bobaedream:'보배드림',manual:'직접 입력'};
  const sourceName = source => sources[source] || '기타';
  const safeURL = value => { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
  const titleOf = l => [l?.vehicle?.make,l?.vehicle?.model,l?.vehicle?.trim].filter(Boolean).join(' ') || '차량 정보 미확인';
  const priceOf = l => l?.vehicle?.price_krw != null ? `${(l.vehicle.price_krw/10000).toLocaleString('ko-KR')}<small>만원</small>` : '가격 미확인';
  const shortDate = value => value ? new Date(value).toLocaleDateString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit'}) : '';
  const dealerKey = d => JSON.stringify([d.source,d.dealer_key]);
  const dealerName = d => d.display_name || '판매자 '+(d.dealer_key.length>12?d.dealer_key.slice(0,6)+'…'+d.dealer_key.slice(-4):d.dealer_key);
  const state = {history:[],dealers:[],view:'garage',filter:'all',dealerFilter:'favorite',search:'',dealerSearch:'',sort:'recent',draft:null,historyId:null,result:null,lookupURL:'',lookupRunning:false,lookupMessage:'',lookupError:false,loading:true,related:null};
  let toastTimer, dialogContext, loadRevision=0;
  function notify(message, error=false){
    clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className='toast'+(error?' error':''); $('toast').hidden=false;
    toastTimer=setTimeout(()=>$('toast').hidden=true,error?8000:3500);
  }
  async function request(url, method='GET', body){
    const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});
    let data; try { data=await response.json(); } catch { throw new Error('서버에 연결할 수 없습니다. 다시 시도해주세요.'); }
    if(!response.ok || data.ok===false) throw new Error(typeof data.detail==='string'?data.detail:data.reason || '요청을 처리하지 못했습니다.');
    return data;
  }
  async function loadData(){
    const revision=++loadRevision;
    const results=await Promise.allSettled([request('/api/history'),request('/api/dealers')]);
    if(revision!==loadRevision) return;
    const errors=[];
    if(results[0].status==='fulfilled') state.history=results[0].value.items; else errors.push('차량 목록을 불러오지 못했습니다.');
    if(results[1].status==='fulfilled') state.dealers=results[1].value.dealers; else errors.push('딜러 목록을 불러오지 못했습니다.');
    state.loading=false;
    $('connectionError').hidden=!errors.length;
    $('connectionError').innerHTML=esc(errors.join(' '))+' <button class="button small secondary" data-action="retry">다시 시도</button>';
    updateNavigation();
  }
  function knownDealers(){
    const merged=new Map();
    for(const item of state.history){
      const d=item.listing?.dealer;
      if(!d?.dealer_id) continue;
      const candidate={source:item.listing.source||item.source,dealer_key:d.dealer_id,display_name:d.display_name||'',phone:d.phone||'',region:d.region||'',favorite:false,blacklisted:false};
      if(!merged.has(dealerKey(candidate))) merged.set(dealerKey(candidate),candidate);
    }
    for(const d of state.dealers){
      const old=merged.get(dealerKey(d));
      merged.set(dealerKey(d),{...old,...d,display_name:d.display_name||old?.display_name||'',phone:d.phone||old?.phone||'',region:d.region||old?.region||''});
    }
    return [...merged.values()];
  }
  function draftDealer(){
    const l=state.draft,d=l?.dealer;
    if(!d?.dealer_id) return null;
    const base={source:l.source||'manual',dealer_key:d.dealer_id,display_name:d.display_name||'',phone:d.phone||'',region:d.region||''};
    const stored=state.dealers.find(item=>dealerKey(item)===dealerKey(base));
    return {...stored,...base,display_name:base.display_name||stored?.display_name||'',phone:base.phone||stored?.phone||'',region:base.region||stored?.region||''};
  }
  function updateNavigation(){
    const navView=state.view==='detail'?'lookup':state.view;
    document.querySelectorAll('[data-nav]').forEach(a=>{if(a.dataset.nav===navView)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    $('breadcrumb').textContent={garage:'차량 보관함',lookup:'매물 확인',dealers:'딜러 관리',detail:'매물 확인 / 차량 상세'}[state.view];
    $('garageCount').textContent=state.history.length;
    $('dealerCount').textContent=state.dealers.filter(d=>d.favorite).length;
  }
  function setView(view){state.view=view;updateNavigation();render();window.scrollTo({top:0});}
  function navigate(view){if(location.hash==='#'+view)setView(view);else location.hash=view;}
  window.addEventListener('hashchange',()=>setView(['garage','lookup','dealers'].includes(location.hash.slice(1))?location.hash.slice(1):'garage'));
  function heading(eyebrow,title,sub,action=''){return `<div class="page-heading"><div><h1>${title}</h1><p class="subtitle">${sub}</p></div>${action}</div>`;}
  function empty(title,description,button=''){return `<div class="empty"><div class="empty-icon">${icon('garage')}</div><h2>${title}</h2><p>${description}</p>${button}</div>`;}
  function newCarButton(){return `<a class="button primary" href="#lookup">${icon('plus')}매물 추가</a>`;}
  function render(){
    if(state.view==='garage') renderGarage();
    else if(state.view==='lookup') renderLookup();
    else if(state.view==='dealers') renderDealers();
    else renderDetail();
  }
  function renderGarage(){
    const favorites=state.history.filter(i=>i.favorite).length,dealers=state.dealers.filter(d=>d.favorite).length;
    $('main').innerHTML=heading('MY GARAGE','차량 보관함','조회한 차량과 찜한 매물을 한곳에서.',newCarButton())+`
      <div class="overview">
        <div class="metric"><div class="metric-icon">${icon('car')}</div><div><div class="metric-label">전체 차량</div><div class="metric-value">${state.history.length}<small>대</small></div></div></div>
        <button class="metric" data-action="show-favorites"><div class="metric-icon">${icon('heart')}</div><div><div class="metric-label">찜한 매물</div><div class="metric-value">${favorites}<small>대</small></div></div></button>
        <a class="metric" href="#dealers"><div class="metric-icon">${icon('user')}</div><div><div class="metric-label">찜한 딜러</div><div class="metric-value">${dealers}<small>명</small></div></div></a>
      </div>
      <div class="toolbar"><div class="tabs" aria-label="차량 필터">
        <button class="tab" data-action="car-filter" data-value="all" aria-pressed="${state.filter==='all'}">전체 차량<span>${state.history.length}</span></button>
        <button class="tab" data-action="car-filter" data-value="favorite" aria-pressed="${state.filter==='favorite'}">찜한 매물<span>${favorites}</span></button>
      </div><div class="search-sort"><label class="search-box">${icon('search')}<input id="carSearch" type="search" aria-label="차량 검색" placeholder="차량명, 사이트 검색" value="${esc(state.search)}"></label>
      <select id="carSort" aria-label="차량 정렬"><option value="recent">최근 조회순</option><option value="price-low">낮은 가격순</option><option value="price-high">높은 가격순</option></select></div></div>
      ${state.related?'<div class="list-meta">선택한 딜러의 매물<button class="button quiet small" data-action="clear-related">필터 해제 ×</button></div>':''}
      <div id="carResults"></div><p class="page-note">가격과 차량 정보는 저장 시점 기준입니다.</p>`;
    $('carSort').value=state.sort; renderCars();
  }
  function renderCars(){
    const search=state.search.trim().toLocaleLowerCase();
    let items=state.history.filter(i=>(state.filter!=='favorite'||i.favorite)&&(!search||[titleOf(i.listing),sourceName(i.source),i.url,i.listing?.vehicle?.region].join(' ').toLocaleLowerCase().includes(search)));
    if(state.related)items=items.filter(i=>dealerKey({source:i.listing?.source||i.source,dealer_key:i.listing?.dealer?.dealer_id})===state.related);
    if(state.sort!=='recent')items.sort((a,b)=>{const x=a.listing?.vehicle?.price_krw,y=b.listing?.vehicle?.price_krw;if(x==null)return y==null?0:1;if(y==null)return -1;return state.sort==='price-low'?x-y:y-x;});
    $('carResults').innerHTML=state.loading?empty('불러오는 중','저장된 차량을 확인하고 있습니다.'):items.length?`<div class="list-meta"><span>${items.length}대의 차량</span><span>차량명을 눌러 상세 확인</span></div><div class="car-list">${items.map(carRow).join('')}</div>`:empty(search||state.related?'검색 결과가 없습니다.':state.filter==='favorite'?'아직 찜한 매물이 없습니다.':'첫 번째 차량을 추가해보세요.',search||state.related?'다른 검색어나 필터를 선택해주세요.':state.filter==='favorite'?'차량 옆 하트를 누르면 여기에 모입니다.':'매물 링크 하나로 차량 확인을 시작할 수 있습니다.',state.filter==='favorite'?'<button class="button secondary" data-action="car-filter" data-value="all">전체 차량 보기</button>':newCarButton());
  }
  function carRow(item){
    const l=item.listing,v=l?.vehicle||{},d=state.dealers.find(d=>d.source===(l?.source||item.source)&&d.dealer_key===l?.dealer?.dealer_id);
    const photo=(l?.photos?.urls||[]).find(u=>safeURL(u)&&!/(logo|assets|bobae\.png)/i.test(u));
    const facts=[v.model_year?`${v.model_year}년`:null,v.mileage_km!=null?`${v.mileage_km.toLocaleString()} km`:null,v.region||null].filter(Boolean).join(' · ');
    return `<article class="car-row"><div class="car-main"><div class="car-visual">${icon('car')}${photo?`<img src="${esc(safeURL(photo))}" alt="" loading="lazy" referrerpolicy="no-referrer">`:''}</div><div class="car-copy"><div class="pills"><span class="pill">${esc(sourceName(item.source))}</span>${item.status==='failed'?'<span class="pill amber">조회 실패</span>':''}${d?.blacklisted?'<span class="pill red">제외 딜러</span>':''}${item.origin==='snapshot'?'<span class="pill">기존 저장</span>':''}</div><button class="car-title" data-action="open-car" data-id="${item.id}">${esc(titleOf(l))}</button><p class="car-facts">${esc(facts||'상세 정보를 입력해주세요.')}</p></div></div>
      <div class="car-price">${priceOf(l)}<br><a class="row-link" href="${esc(safeURL(item.url))}" target="_blank" rel="noopener noreferrer">원문 보기${icon('arrow')}</a></div>
      <button class="icon-button" data-action="favorite-car" data-id="${item.id}" aria-pressed="${item.favorite}" aria-label="${esc(titleOf(l))} ${item.favorite?'찜 해제':'찜하기'}">${icon('heart')}</button></article>`;
  }
  function renderLookup(){
    $('main').innerHTML=heading('CHECK A CAR','매물 확인','마음에 드는 차량의 링크를 붙여넣어 주세요.')+`<div class="lookup-layout"><section class="panel lookup-panel"><h2>이 차, 조금 더 살펴볼까요?</h2><form id="lookupForm" class="lookup-form"><label class="field-label" for="lookupURL">매물 링크</label><div class="url-row"><input id="lookupURL" type="url" required placeholder="https://…" value="${esc(state.lookupURL)}"><button class="button primary" type="submit" ${state.lookupRunning?'disabled':''}>${state.lookupRunning?'불러오는 중…':'차량 불러오기'}${icon('arrow')}</button></div></form><div class="source-hints"><span>엔카</span><span>케이카</span><span>KB차차차</span><span>보배드림</span></div><div id="lookupMessage" class="inline-message${state.lookupError?' error':''}" role="status" ${!state.lookupMessage?'hidden':''}>${esc(state.lookupMessage)}</div><div class="manual-row"><span>링크가 없거나 불러오지 못하셨나요?</span><button class="button quiet small" data-action="manual">직접 입력${icon('arrow')}</button></div></section><div class="steps"><div><p class="number">01 / 불러오기</p><h3>차량 정보 확인</h3><p>가격과 기본 정보를<br>한곳에서 확인하세요.</p></div><div><p class="number">02 / 확인하기</p><h3>핵심 이력 체크</h3><p>기록부와 보험이력으로<br>빠진 항목을 채워보세요.</p></div><div><p class="number">03 / 보관하기</p><h3>마음에 들면 찜</h3><p>차량과 딜러를 저장해<br>다시 비교해보세요.</p></div></div></div>`;
  }
  async function lookup(url){
    if(state.lookupRunning)return;
    state.lookupURL=url;state.lookupRunning=true;state.lookupError=false;state.lookupMessage='차량 정보를 불러오고 있습니다. 잠시만 기다려주세요.';renderLookup();
    try{
      const response=await fetch('/api/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      const data=await response.json();
      await loadData();
      if(!response.ok||!data.ok)throw new Error(typeof data.detail==='string'?data.detail:data.reason||'차량 정보를 불러오지 못했습니다.');
      state.lookupMessage='';
      if(state.view==='lookup')openListing(data.listing,data.history_id,url);
      else notify('차량을 보관함에 저장했습니다.');
    }catch(err){state.lookupError=true;state.lookupMessage=err.message;}
    finally{state.lookupRunning=false;if(state.view==='lookup')renderLookup();}
  }
  function openListing(listing,id=null,url=''){
    state.draft=structuredClone(listing||{});state.draft.url=url||state.draft.url||'';state.draft.source=state.draft.source||'manual';state.historyId=id;state.result=null;
    setView('detail');
  }
  function field(id,label,value='',type='text',extra=''){
    return `<div><label class="field-label" for="${id}">${label}</label><input id="${id}" name="${id}" type="${type}" value="${esc(value??'')}" ${type==='number'?'min="0"':''} ${extra}></div>`;
  }
  const checks=[['frame_ok','프레임 손상','무손상 확인','손상 있음',true],['flood_damage','침수 이력','없음 확인','있음',false],['total_loss','전손 이력','없음 확인','있음',false],['theft','도난 이력','없음 확인','있음',false],['history_disclosed','보험이력 조회','조회 가능','비공개 / 거부',true]];
  function renderDetail(){
    const l=state.draft;if(!l){setView('lookup');return;}
    const v=l.vehicle||{},d=l.dealer||{},ih=l.insurance_history||{};
    const item=state.history.find(i=>i.id===state.historyId);
    $('main').innerHTML=`<button class="back-link" data-action="back">${icon('back')}차량 보관함</button><div class="detail-title"><div><div class="pills"><span class="pill">${esc(sourceName(l.source))}</span>${item?`<span class="pill">${esc(shortDate(item.last_viewed_at))} 저장</span>`:''}</div><h1>${esc(titleOf(l)==='차량 정보 미확인'?'차량 정보 확인':titleOf(l))}</h1><p class="subtitle">${item?'저장된 정보를 확인하고 필요한 항목을 보완하세요.':'차량 정보와 핵심 이력을 확인해주세요.'}</p></div>${item?`<button class="icon-button" data-action="favorite-car" data-id="${item.id}" aria-pressed="${item.favorite}" aria-label="${item.favorite?'매물 찜 해제':'매물 찜하기'}">${icon('heart')}</button>`:''}</div>
      ${item?.status==='failed'?'<p class="inline-message error">최근 조회에 실패했습니다. 이전 저장 정보를 확인하거나 직접 입력해주세요.</p>':''}
      <div class="detail-layout"><div><div id="verificationResult" tabindex="-1"></div><form id="vehicleForm">
      <section class="panel"><div class="panel-heading"><h2><span class="step-number">01</span>차량 정보</h2>${safeURL(l.url)?`<a class="row-link" href="${esc(safeURL(l.url))}" target="_blank" rel="noopener noreferrer">원문 보기${icon('arrow')}</a>`:''}</div>
      <div class="form-grid three">${field('make','제조사',v.make)}${field('model','모델',v.model)}${field('trim','트림',v.trim)}</div><div class="form-grid three">${field('year','연식',v.model_year,'number','max="2100"')}${field('mileage','주행거리 · km',v.mileage_km,'number')}${field('price','가격 · 만원',v.price_krw!=null?v.price_krw/10000:'','number','step="0.01"')}</div></section>
      <section class="panel"><div class="panel-heading"><h2><span class="step-number">02</span>핵심 이력</h2><span class="pill green" id="checkProgress"></span></div><p class="form-note" style="margin-bottom:20px">기록부에서 확인한 항목만 선택해주세요. 미확인 항목은 구매 보류로 처리됩니다.</p><div>${checks.map(([key,label,good,bad,goodValue])=>{const value=key==='frame_ok'?l.performance_record?.third_party_inspection?.frame_ok:ih[key];return `<fieldset class="check-row"><legend>${label}</legend><div class="tri">${[[goodValue,good],[!goodValue,bad],[null,'미확인']].map(([val,text])=>`<label><input type="radio" name="${key}" value="${val}" ${(value??null)===val?'checked':''}><span>${text}</span></label>`).join('')}</div>${key==='history_disclosed'?`<label class="check-extra"><input type="checkbox" id="infoGap" ${ih.info_unavailable_periods?.length?'checked':''}>조회되지 않는 기간이 있었음</label>`:''}</fieldset>`;}).join('')}</div></section>
      <details class="panel details-panel"><summary>추가 정보 · 딜러 정보 수정</summary><div class="details-content"><div class="form-grid">${field('region','차량 지역',v.region)}${field('ownerChange','명의변경 횟수',ih.owner_change_count??0,'number')}<div><label class="field-label" for="accident">판매자 사고 고지</label><select id="accident"><option value="">모름 / 없음</option><option value="사고있음" ${(l.listing_text?.claims_parsed||[]).includes('사고있음')?'selected':''}>사고 있음</option></select></div><div><label class="field-label" for="source">사이트</label><select id="source">${Object.entries(sources).map(([key,label])=>`<option value="${key}" ${key===l.source?'selected':''}>${label}</option>`).join('')}</select></div>${field('listingURL','매물 링크',l.url,'url')}${field('dealerName','딜러 이름',d.display_name)}${field('dealerId','사이트별 딜러 ID',d.dealer_id)}${field('dealerPhone','연락처',d.phone,'tel')}${field('dealerRegion','딜러 지역',d.region)}</div></div></details>
      <div class="form-footer"><p class="form-note">입력한 내용은 검증 시 저장됩니다.</p><button class="button primary" id="verifyButton" type="submit">저장하고 검증하기${icon('arrow')}</button></div><p id="verifyError" class="error-text" role="alert"></p></form></div><aside class="detail-aside"><div id="detailDealer"></div><section class="panel"><div class="panel-heading"><h2>확인할 자료</h2></div><div class="source-links">${renderSourceLinks(l)}</div>${safeURL(l.url)?'<button class="button secondary full" style="margin-top:22px" data-action="reload-car">최신 정보 다시 불러오기</button>':''}</section></aside></div>`;
    renderDetailDealer();updateProgress();renderResult();
  }
  function renderSourceLinks(l){
    const links=(l.verification_links||[]).filter(link=>safeURL(link.url));
    if(!links.length&&safeURL(l.url))links.push({label:'매물 원문 · 성능기록부',url:l.url});
    return links.length?links.map(link=>`<a href="${esc(safeURL(link.url))}" target="_blank" rel="noopener noreferrer">${esc(link.label)}${icon('arrow')}</a>`).join(''):'<p class="form-note">판매자에게 성능기록부와 보험이력을 요청해주세요.</p>';
  }
  function updateProgress(){
    if(!$('checkProgress'))return;
    const n=checks.filter(([key])=>document.querySelector(`input[name="${key}"]:checked`)?.value!=='null').length;
    $('checkProgress').textContent=`${n} / 5 확인`;
  }
  function collectListing(){
    const listing=structuredClone(state.draft||{}),number=id=>$(id).value===''?null:Number($(id).value);
    listing.listing_id=listing.listing_id||'manual-'+Date.now();listing.source=$('source').value;listing.url=$('listingURL').value.trim();
    listing.vehicle={...listing.vehicle,make:$('make').value.trim(),model:$('model').value.trim(),trim:$('trim').value.trim(),model_year:number('year'),mileage_km:number('mileage'),price_krw:number('price')===null?null:Math.round(number('price')*10000),region:$('region').value.trim()};
    listing.dealer={...listing.dealer,dealer_id:$('dealerId').value.trim(),display_name:$('dealerName').value.trim(),phone:$('dealerPhone').value.trim(),region:$('dealerRegion').value.trim()};
    listing.insurance_history={...listing.insurance_history,owner_change_count:number('ownerChange')??0};
    listing.performance_record={...listing.performance_record,third_party_inspection:{...listing.performance_record?.third_party_inspection}};
    for(const [key] of checks){const value=JSON.parse(document.querySelector(`input[name="${key}"]:checked`).value);if(key==='frame_ok')listing.performance_record.third_party_inspection.frame_ok=value;else listing.insurance_history[key]=value;}
    listing.insurance_history.info_unavailable_periods=$('infoGap').checked?(listing.insurance_history.info_unavailable_periods?.length?listing.insurance_history.info_unavailable_periods:[{start:null,end:null}]):[];
    listing.listing_text={...listing.listing_text,claims_parsed:[...(listing.listing_text?.claims_parsed||[]).filter(c=>c!=='사고있음'),...($('accident').value?['사고있음']:[])]};
    return listing;
  }
  async function verify(){
    state.draft=collectListing();const submitted=structuredClone(state.draft);const button=$('verifyButton');button.disabled=true;button.textContent='검증 중…';$('verifyError').textContent='';
    try{const result=await request('/api/verify','POST',{listing:submitted});await loadData();
      if(state.view==='detail'&&JSON.stringify(state.draft)===JSON.stringify(submitted)){state.result=result;state.historyId=state.history.find(i=>safeURL(i.url)===safeURL(submitted.url))?.id||state.historyId;renderDetail();$('verificationResult').focus();$('verificationResult').scrollIntoView({block:'start',behavior:'smooth'});}else notify('검증 내용을 저장했습니다.');
    }catch(err){if($('verifyError'))$('verifyError').textContent=err.message;else notify(err.message,true);}
    finally{if($('verifyButton')){$('verifyButton').disabled=false;$('verifyButton').innerHTML='저장하고 검증하기'+icon('arrow');}}
  }
  function renderResult(){
    const el=$('verificationResult');if(!el)return;const result=state.result;$('vehicleForm').hidden=!!result;if(!result){el.innerHTML='';return;}
    const proceed=result.overall==='proceed';
    const ordered=[...result.items].sort((a,b)=>({fail:0,unknown:1,pass:2}[a.verdict]-{fail:0,unknown:1,pass:2}[b.verdict]));
    const missing=result.items.filter(i=>i.critical&&i.verdict==='unknown').length,failed=result.items.filter(i=>i.critical&&i.verdict==='fail').length;
    el.innerHTML=`<div class="result-banner ${proceed?'proceed':''}"><h2>${proceed?'핵심 항목 통과':'구매 보류 · 추가 확인 필요'}</h2><p>${proceed?'확인된 핵심 항목에서 결격 사유가 발견되지 않았습니다.':`미확인 ${missing}개 · 결격 ${failed}개. 아래 항목을 확인해주세요.`}</p></div><div class="result-list">${ordered.map(i=>`<details class="result-item" ${i.critical&&i.verdict!=='pass'?'open':''}><summary><span>${esc(i.label)}${i.critical?'<small>핵심</small>':''}</span><span class="pill ${i.verdict==='pass'?'green':i.verdict==='fail'?'red':'amber'}">${{pass:'통과',fail:'결격',unknown:'미확인'}[i.verdict]}</span></summary><p>${esc(i.detail)}</p></details>`).join('')}</div><div class="form-footer" style="margin-bottom:24px"><p class="form-note">저장된 정보와 입력한 답변에 따른 결과입니다.</p><button class="button secondary" data-action="edit-verification">입력 내용 수정</button></div>`;
  }
  function renderDetailDealer(){
    const el=$('detailDealer');if(!el)return;const d=draftDealer();
    el.innerHTML=`<section class="panel"><div class="panel-heading"><h2>판매 딜러</h2></div>${d?dealerContent(d,'detail'):'<p class="form-note">딜러 정보가 없습니다.<br>추가 정보에서 사이트와 딜러 ID를 입력하면 찜하거나 제외할 수 있습니다.</p>'}</section>`;
  }
  function dealerContent(d,context){
    const key=esc(dealerKey(d)),telephone=(d.phone||'').replace(/[^\d+]/g,'');
    return `<div class="dealer-card-top"><div class="dealer-identity"><div class="dealer-avatar">${icon('user')}</div><div><div class="dealer-name">${esc(dealerName(d))}</div><div class="dealer-source">${esc(sourceName(d.source))}</div></div></div>${d.blacklisted?'<span class="pill red" style="align-self:flex-start">블랙리스트</span>':''}</div>
      ${telephone?`<a class="contact-line" href="tel:${esc(telephone)}">${icon('phone')}${esc(d.phone)}</a>`:'<div class="contact-line">연락처 미등록</div>'}${d.region?`<div class="contact-line">${icon('pin')}${esc(d.region)}</div>`:''}
      <details class="dealer-key"><summary>딜러 ID 확인</summary><p>${esc(d.dealer_key)}</p></details>
      ${d.blacklisted?`<div class="reason">${esc(d.reason||'사유 미기재')}<time>${esc(shortDate(d.blacklisted_at))} 등록</time></div>`:''}
      <div class="dealer-actions">${d.blacklisted?`<button class="button secondary small" data-action="unblock-dealer" data-key="${key}" data-context="${context}">블랙리스트 해제</button>`:`<button class="button secondary small ${d.favorite?'selected':''}" data-action="favorite-dealer" data-key="${key}" data-context="${context}" aria-pressed="${!!d.favorite}">${icon('heart')}${d.favorite?'찜 해제':'딜러 찜하기'}</button><button class="button quiet small" data-action="block-dealer" data-key="${key}" data-context="${context}">제외 등록</button>`}</div>`;
  }
  function renderDealers(){
    const all=knownDealers(),fav=all.filter(d=>d.favorite).length,blocked=all.filter(d=>d.blacklisted).length;
    $('main').innerHTML=heading('MY DEALERS','딜러 관리','다시 찾을 딜러와 피할 딜러를 구분해두세요.','<button class="button primary" data-action="add-dealer">'+icon('plus')+'딜러 추가</button>')+`
      <div class="toolbar"><div class="tabs" aria-label="딜러 필터">${[['favorite','찜 딜러',fav],['blocked','블랙리스트',blocked],['all','전체',all.length]].map(([key,label,count])=>`<button class="tab" data-action="dealer-filter" data-value="${key}" aria-pressed="${state.dealerFilter===key}">${label}<span>${count}</span></button>`).join('')}</div><label class="search-box">${icon('search')}<input type="search" id="dealerSearch" aria-label="딜러 검색" placeholder="이름, 연락처, ID 검색" value="${esc(state.dealerSearch)}"></label></div><div id="dealerResults"></div>`;renderDealerCards();
  }
  function renderDealerCards(){
    const query=state.dealerSearch.trim().toLocaleLowerCase();
    const rows=knownDealers().filter(d=>(state.dealerFilter==='all'||(state.dealerFilter==='favorite'?d.favorite:d.blacklisted))&&[d.display_name,d.phone,d.dealer_key,d.region,sourceName(d.source)].join(' ').toLocaleLowerCase().includes(query));
    $('dealerResults').innerHTML=state.loading?empty('불러오는 중','저장된 딜러를 확인하고 있습니다.'):rows.length?`<div class="list-meta">${rows.length}명의 딜러</div><div class="dealer-grid">${rows.map(d=>{const count=state.history.filter(i=>i.listing?.dealer?.dealer_id===d.dealer_key&&(i.listing.source||i.source)===d.source).length;return `<article class="dealer-card ${d.blacklisted?'blocked':''}">${dealerContent(d,'list')}${count?`<button class="related-link" data-action="dealer-cars" data-key="${esc(dealerKey(d))}">보관한 차량 ${count}대 보기 →</button>`:''}</article>`;}).join('')}</div>`:empty(query?'검색 결과가 없습니다.':state.dealerFilter==='blocked'?'블랙리스트가 비어 있습니다.':state.dealerFilter==='favorite'?'아직 찜한 딜러가 없습니다.':'저장된 딜러가 없습니다.',query?'다른 이름이나 연락처로 검색해주세요.':state.dealerFilter==='blocked'?'제외 등록한 딜러와 사유를 여기에서 확인할 수 있습니다.':'차량 상세에서 딜러를 찜하거나 직접 추가해보세요.',state.dealerFilter==='favorite'?'<button class="button secondary" data-action="dealer-filter" data-value="all">전체 딜러 보기</button>':'');
  }
  function findDealer(button){return button.dataset.context==='detail'?draftDealer():knownDealers().find(d=>dealerKey(d)===button.dataset.key);}
  function dealerPayload(d){return {source:d.source,dealer_key:d.dealer_key,display_name:d.display_name||'',phone:d.phone||'',region:d.region||''};}
  async function refreshAfterDealer(){
    const previousResult=state.result;
    await loadData();
    if(state.view==='detail'){
      if(previousResult){
        try{state.result=await request('/api/verify','POST',{listing:state.draft});}
        catch{state.result=null;notify('딜러 상태는 저장했습니다. 검증 결과는 다시 확인해주세요.',true);}
      }
      renderDetailDealer();renderResult();
    }else render();
  }
  async function favoriteDealer(button){
    const d=findDealer(button);if(!d)return;button.disabled=true;
    try{await request('/api/dealers/favorite','POST',{...dealerPayload(d),favorite:!d.favorite});await refreshAfterDealer();notify(d.favorite?'딜러 찜을 해제했습니다.':'찜 딜러에 저장했습니다.');}
    catch(err){notify(err.message,true);button.disabled=false;}
  }
  async function unblockDealer(button){
    const d=findDealer(button);if(!d)return;button.disabled=true;
    try{await request('/api/dealers/unblacklist','POST',{source:d.source,dealer_key:d.dealer_key});await refreshAfterDealer();notify('블랙리스트를 해제했습니다.');}
    catch(err){notify(err.message,true);button.disabled=false;}
  }
  function openDealerDialog(d=null){
    dialogContext=d; $('dialogError').textContent='';$('dialogSubmit').disabled=false;
    $('dialogTitle').textContent=d?'블랙리스트 등록':'찜 딜러 추가';$('dialogSubmit').textContent=d?'제외 등록':'찜 딜러에 저장';$('dialogSubmit').className='button '+(d?'danger':'primary');
    $('dialogBody').innerHTML=d?`<p class="dialog-description">${esc(d.display_name||sourceName(d.source)+' 딜러')}의 매물은 검증 시 구매 보류로 표시됩니다.${d.favorite?' 기존 딜러 찜도 해제됩니다.':''}</p><label class="field-label" for="blockReason">제외 사유</label><textarea id="blockReason" required maxlength="1000" placeholder="예: 성능기록부와 실제 상태가 다름"></textarea>`:`<p class="dialog-description">같은 이름의 딜러를 구분하기 위해 사이트와 ID가 필요합니다.</p><div class="dialog-form-fields"><div><label class="field-label" for="newDealerSource">사이트</label><select id="newDealerSource">${Object.entries(sources).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></div>${field('newDealerId','사이트별 딜러 ID','','text','required')}${field('newDealerName','딜러 이름')}${field('newDealerPhone','연락처','','tel')}${field('newDealerRegion','지역')}</div>`;
    $('dealerDialog').showModal();
  }
  $('closeDialog').onclick=$('cancelDialog').onclick=()=>$('dealerDialog').close();
  $('dealerDialogForm').addEventListener('submit',async event=>{
    event.preventDefault();const d=dialogContext;const button=$('dialogSubmit');button.disabled=true;$('dialogError').textContent='';
    try{
      if(d){const reason=$('blockReason').value.trim();if(!reason)throw new Error('제외 사유를 입력해주세요.');await request('/api/dealers/blacklist','POST',{...dealerPayload(d),reason});}
      else {const key=$('newDealerId').value.trim();if(!key)throw new Error('딜러 ID를 입력해주세요.');await request('/api/dealers/favorite','POST',{source:$('newDealerSource').value,dealer_key:key,display_name:$('newDealerName').value.trim(),phone:$('newDealerPhone').value.trim(),region:$('newDealerRegion').value.trim(),favorite:true});}
      $('dealerDialog').close();await refreshAfterDealer();notify(d?'블랙리스트에 등록했습니다.':'찜 딜러에 저장했습니다.');
    }catch(err){$('dialogError').textContent=err.message;}finally{button.disabled=false;}
  });
  async function favoriteCar(button){
    const item=state.history.find(i=>i.id===Number(button.dataset.id));if(!item)return;button.disabled=true;
    try{const data=await request(`/api/history/${item.id}/favorite`,'PATCH',{favorite:!item.favorite});item.favorite=data.favorite;if(state.view==='detail'){button.setAttribute('aria-pressed',String(item.favorite));button.setAttribute('aria-label',item.favorite?'매물 찜 해제':'매물 찜하기');button.disabled=false;}else renderGarage();notify(item.favorite?'찜한 매물에 저장했습니다.':'매물 찜을 해제했습니다.');}
    catch(err){notify(err.message,true);button.disabled=false;}
  }
  document.addEventListener('click',async event=>{
    const link=event.target.closest('a[href^="#"]');
    if(link && ['#garage','#lookup','#dealers'].includes(link.getAttribute('href'))){event.preventDefault();navigate(link.getAttribute('href').slice(1));return;}
    const button=event.target.closest('[data-action]');if(!button)return;
    switch(button.dataset.action){
      case 'retry':button.disabled=true;await loadData();render();break;
      case 'car-filter':state.filter=button.dataset.value;renderGarage();break;
      case 'show-favorites':state.filter='favorite';renderGarage();break;
      case 'favorite-car':await favoriteCar(button);break;
      case 'open-car':{const item=state.history.find(i=>i.id===Number(button.dataset.id));openListing(item.listing,item.id,item.url);break;}
      case 'back':navigate('garage');break;
      case 'edit-verification':state.result=null;renderResult();$('model').focus();break;
      case 'manual':openListing({},null,state.lookupURL);break;
      case 'reload-car':state.lookupURL=state.draft.url;state.lookupMessage='';setView('lookup');lookup(state.lookupURL);break;
      case 'dealer-filter':state.dealerFilter=button.dataset.value;renderDealers();break;
      case 'favorite-dealer':await favoriteDealer(button);break;
      case 'block-dealer':openDealerDialog(findDealer(button));break;
      case 'unblock-dealer':await unblockDealer(button);break;
      case 'add-dealer':openDealerDialog();break;
      case 'dealer-cars':state.related=button.dataset.key;state.filter='all';state.search='';navigate('garage');break;
      case 'clear-related':state.related=null;renderGarage();break;
    }
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='carSearch'){state.search=event.target.value;renderCars();}
    if(event.target.id==='dealerSearch'){state.dealerSearch=event.target.value;renderDealerCards();}
    if(event.target.id==='lookupURL')state.lookupURL=event.target.value;
    if(event.target.closest('#vehicleForm')){state.draft=collectListing();state.result=null;updateProgress();renderResult();if(['dealerId','dealerName','dealerPhone','dealerRegion','source'].includes(event.target.id))renderDetailDealer();}
  });
  document.addEventListener('change',event=>{if(event.target.id==='carSort'){state.sort=event.target.value;renderCars();}});
  document.addEventListener('submit',event=>{
    if(event.target.id==='lookupForm'){event.preventDefault();lookup($('lookupURL').value.trim());}
    if(event.target.id==='vehicleForm'){event.preventDefault();verify();}
  });
  document.addEventListener('error',event=>{if(event.target.tagName==='IMG')event.target.hidden=true;},true);
  state.view=['garage','lookup','dealers'].includes(location.hash.slice(1))?location.hash.slice(1):'garage';
  updateNavigation();render();loadData().then(()=>render());
})();
