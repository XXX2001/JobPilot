// Adapted from browser-use/jev-ultrafast, jev_ultrafast/snapshot.js (MIT License).
// https://github.com/browser-use/jev-ultrafast
(limits => {
  if (!document.body) return null;
  const cache = window.__jobpilotHarness ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const query = (root, selector) => {
    const found=[];
    for (const e of root.querySelectorAll('*')) {
      if (e.matches(selector)) found.push(e);
      if (e.shadowRoot) found.push(...query(e.shadowRoot,selector));
    }
    return found;
  };
  const closest = (e, selector) => {
    while (e) {
      const match=e.closest(selector);
      if (match) return match;
      e=e.getRootNode().host;
    }
    return null;
  };
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !closest(e,'[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const children = e => e.tagName==='SLOT' ? e.assignedNodes({flatten:true}) :
    (e.shadowRoot||e).childNodes;
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(e.getRootNode().getElementById?.(id)||document.getElementById(id),seen))
      .filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...children(e)].map(n=>
        n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const clip = value => value.replace(/\s+/g,' ').trim().slice(0,limits.label_chars);
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  const submitPattern=new RegExp(limits.submit_pattern,'i');
  const submits = e => (e.tagName==='INPUT' && ['submit','image'].includes(e.type)) ||
    (e.tagName==='BUTTON' && e.type==='submit' && !!e.form) ||
    (['button','link'].includes(role(e)) && submitPattern.test(name(e)));
  const files = e => [...(e.files||[])].map(f=>[f.name,f.size,f.lastModified]);
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    query(document,'input,textarea,select').filter(e=>safe(e)||e.type==='file')
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly,files(e)])];
  cache.guard=(e,upload=false)=>{
    if (!e?.isConnected || (upload ? e.type!=='file' : !safe(e)||!visible(e))) return null;
    const scope=closest(e,'form,dialog,[role="dialog"],article,li,tr,[role="row"]') ||
      e.parentElement || e.getRootNode().host;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||'',submits(e),files(e),
      e.tagName==='SELECT' ? [...e.options].map(o=>[o.value,o.label,o.disabled,
        !!o.closest('optgroup[disabled]')]) : null];
  };
  cache.visible=visible;
  cache.closest=closest;
  cache.hitTest=(e,x,y)=>{
    let hit=document.elementFromPoint(x,y);
    while (hit?.shadowRoot) {
      const inner=hit.shadowRoot.elementFromPoint(x,y);
      if (!inner || inner===hit) break;
      hit=inner;
    }
    while (hit) {
      if (e===hit || e.contains(hit)) return true;
      hit=hit.getRootNode().host;
    }
    return false;
  };
  const actions=[], uploads=[];
  for (const e of query(document,selector)) {
    if (e.type==='file') {
      uploads.push({node:identity(e),label:clip(name(e)||e.parentElement?.innerText||e.name||e.id||'File'),
        name:e.name||'',input_id:e.id||'',accept:e.accept||'',filled:!!e.files?.length});
      continue;
    }
    if (!safe(e) || !visible(e) || e.matches(':disabled') || closest(e,'[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:clip(name(e)||rname),submits:submits(e),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      delete base.selected;
      const options=[...e.options].filter(o=>!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        .slice(0,limits.max_select_options);
      for (const o of options) {
        const optLabel=clip(o.label).slice(0,limits.label_chars-3);
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),
          label:base.label.slice(0,limits.label_chars-3-optLabel.length)+' → '+optLabel});
      }
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      // Guards need the entire accessible name, including an actually empty name.
      // decide.build_request whitelists model fields and never serializes full_label.
      const full_label=name(e).replace(/\s+/g,' ').trim();
      actions.push({...base,kind:editable?'fill':'click',value,
        ...(!editable ? {full_label} : {})});
      if (editable) actions.push({...base,kind:'click',value,full_label,
        label:clip('Open '+base.label)});
    }
  }
  const words=[], range=document.createRange(); let length=0;
  const walk = root => {
    for (const node of children(root)) {
      if (length>=6000) break;
      if (node.nodeType===1) {
        if (node.matches('script,style,noscript,template')) continue;
        walk(node);
      } else if (node.nodeType===3) {
        const value=node.textContent.trim();
        let parent=node.parentElement||node.getRootNode().host;
        while (parent?.tagName==='SLOT') parent=parent.parentElement||parent.getRootNode().host;
        if (!value || !parent || !visible(parent)) continue;
        range.selectNodeContents(node); const r=range.getBoundingClientRect();
        if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
          words.push(value); length+=value.length;
        }
      }
    }
  };
  walk(document.body);
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  for (const u of uploads) guards[u.node]=cache.guard(cache.nodes.get(u.node),true);
  // Compare meaning and identity. Geometry is resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],uploads];
  const omitted_actions=Math.max(0,actions.length-limits.max_actions);
  actions.splice(limits.max_actions);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,uploads,marker,page_key,guards,omitted_actions};
})
