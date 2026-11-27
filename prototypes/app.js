// Prototype only: fake data, no backend. Hash router + tiny helpers.
const I = {
  search:'<path d="m21 21-4.3-4.3"/><circle cx="11" cy="11" r="7"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  cart:'<path d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.6L21 8H6"/><circle cx="10" cy="20" r="1"/><circle cx="18" cy="20" r="1"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  board:'<rect x="3" y="3" width="7" height="18" rx="1.5"/><rect x="14" y="3" width="7" height="11" rx="1.5"/>',
  users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  inbox:'<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
  copy:'<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  box:'<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  tag:'<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  cash:'<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  x:'<path d="M18 6 6 18M6 6l12 12"/>',
  upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
  back:'<path d="M19 12H5M11 6l-6 6 6 6"/>',
  check:'<path d="M20 6 9 17l-5-5"/>',
  alert:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  camera:'<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  trash:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  more:'<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  calendar:'<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  logout:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  home:'<path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2h-4v-7H9v7H5a2 2 0 0 1-2-2z"/>',
  send:'<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
};
const ic = (n, s=16) => '<svg width="'+s+'" height="'+s+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+I[n]+'</svg>';
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const cop = n => '$ ' + Math.round(n).toLocaleString('es-CO');
// Plate color follows the real plate: PARTICULAR yellow, PÚBLICO white, REMOLQUE green.
const plateTone = t => !t ? '' : /REMOLQUE/.test(t) ? ' trailer' : /PÚBLICO/.test(t) ? ' white' : '';
const plate = (p, type) => '<span class="plate'+(type===true?' white':plateTone(type))+'"'+(type&&type!==true?' title="'+type+'"':'')+'>'+p+'</span>';

// ---------- fake data ----------
const D = {
  types: ['CARRO PARTICULAR','MOTO PARTICULAR','CARRO PÚBLICO','MOTO CARRO PARTICULAR','MOTO CARRO PÚBLICO','REMOLQUE','ORNAMENTAL'],
  prices: {'CARRO PARTICULAR':98000,'MOTO PARTICULAR':52000,'CARRO PÚBLICO':104000,'MOTO CARRO PARTICULAR':61000,'MOTO CARRO PÚBLICO':63000,'REMOLQUE':58000,'ORNAMENTAL':45000},
  pay: ['EFECTIVO','TRANSFERENCIA','CRÉDITO','OTROS'],
  customers: [
    {id:1,name:'TRÁMITES DEL VALLE',type:'GESTOR',doc:'NIT 901.234.567-1',phone:'317 000 1122',city:'PALMIRA',orders:148,portal:'activo'},
    {id:2,name:'INGENIO LA ESPERANZA',type:'EMPRESA',doc:'NIT 890.111.222-3',phone:'602 270 0000',city:'PALMIRA',orders:62,portal:'activo'},
    {id:3,name:'GESTORA ANDRADE',type:'GESTOR',doc:'CC 31.234.567',phone:'315 222 3344',city:'CALI',orders:97,portal:'invitado'},
    {id:4,name:'TRÁNSITO CANDELARIA',type:'TRÁNSITO',doc:'NIT 800.555.666-0',phone:'602 264 1111',city:'CANDELARIA',orders:41,portal:'sin acceso'},
    {id:5,name:'MOTOS EL CENTRO',type:'EMPRESA',doc:'NIT 900.777.888-5',phone:'318 444 5566',city:'PALMIRA',orders:33,portal:'sin acceso'},
    {id:6,name:'CARLOS MEDINA',type:'CLIENTE',doc:'CC 16.789.012',phone:'300 123 4567',city:'PALMIRA',orders:2,portal:'sin acceso'},
  ],
  orders: [
    {id:901,n:'4127',date:'18/11/2026',cust:'TRÁMITES DEL VALLE',plates:['KWD91G','LMP22F'],type:'MOTO PARTICULAR',total:104000,pay:'TRANSFERENCIA',status:'pending',due:'Hoy',decl:false,src:'Portal'},
    {id:902,n:'4126',date:'18/11/2026',cust:'INGENIO LA ESPERANZA',plates:['R12345'],type:'REMOLQUE',total:58000,pay:'CRÉDITO',status:'pending',due:'20/11',decl:false,src:'Portal'},
    {id:903,n:'4125',date:'18/11/2026',cust:'CARLOS MEDINA',plates:['HMF922'],type:'CARRO PARTICULAR',total:196000,pay:'EFECTIVO',status:'pending',due:'Vencida',decl:true,src:'Mostrador'},
    {id:904,n:'4124',date:'17/11/2026',cust:'GESTORA ANDRADE',plates:['923NAA'],type:'MOTO CARRO PARTICULAR',total:61000,pay:'EFECTIVO',status:'in_progress',due:'Hoy',decl:false,src:'Mostrador'},
    {id:905,n:'4123',date:'17/11/2026',cust:'TRÁMITES DEL VALLE',plates:['JTR418','JTR419','JTR420'],type:'CARRO PARTICULAR',total:588000,pay:'TRANSFERENCIA',status:'in_progress',due:'19/11',decl:false,src:'Portal'},
    {id:906,n:'4122',date:'17/11/2026',cust:'MOTOS EL CENTRO',plates:['XYZ12A','XYZ13A'],type:'MOTO PARTICULAR',total:104000,pay:'CRÉDITO',status:'ready',due:'Hoy',decl:false,src:'Mostrador'},
    {id:907,n:'4121',date:'16/11/2026',cust:'TRÁNSITO CANDELARIA',plates:['SPQ301'],type:'CARRO PÚBLICO',total:208000,pay:'TRANSFERENCIA',status:'ready',due:'18/11',decl:true,src:'Mostrador'},
    {id:908,n:'4120',date:'16/11/2026',cust:'CARLOS MEDINA',plates:['DFG55B'],type:'MOTO PARTICULAR',total:52000,pay:'EFECTIVO',status:'delivered',due:'—',decl:false,src:'Mostrador'},
  ],
  statuses: {pending:['Por realizar',''],in_progress:['En realización','blue'],ready:['Lista para entregar','green'],delivered:['Entregada','ink']},
  requests: [
    {id:'P-0231',cust:'TRÁMITES DEL VALLE',when:'Hace 12 min',plates:['KWD91G','LMP22F','HTQ45D','JTR500','JTR501'],type:'MOTO PARTICULAR',pay:'TRANSFERENCIA',note:'Entregar con guía a Cali'},
    {id:'P-0042',cust:'INGENIO LA ESPERANZA',when:'Hace 1 h',plates:['R12345','R12346'],type:'REMOLQUE',pay:'CRÉDITO',note:''},
    {id:'P-0107',cust:'TRÁNSITO CANDELARIA',when:'Ayer',plates:['SPQ303'],type:'CARRO PÚBLICO',pay:'TRANSFERENCIA',note:''},
  ],
};
const statusBadge = s => '<span class="badge '+D.statuses[s][1]+'">'+D.statuses[s][0]+'</span>';
const dueBadge = d => d==='Vencida' ? '<span class="badge red">Vencida</span>' : d==='Hoy' ? '<span class="badge amber">Entrega hoy</span>' : d==='—' ? '' : '<span class="badge plain">Entrega '+d+'</span>';

// ---------- overlays ----------
function closeOverlays(){ $$('.scrim,.sheet,.dialog').forEach(e=>e.remove()); }
function sheet(title, body, footer='', sub=''){
  closeOverlays();
  document.body.insertAdjacentHTML('beforeend','<div class="scrim" onclick="closeOverlays()"></div><aside class="sheet" role="dialog" aria-label="'+title+'"><header><div><h2>'+title+'</h2>'+(sub?'<div class="muted small">'+sub+'</div>':'')+'</div><span style="flex:1"></span><button class="btn ghost icon" onclick="closeOverlays()" aria-label="Cerrar">'+ic('x')+'</button></header><div class="body">'+body+'</div>'+(footer?'<footer>'+footer+'</footer>':'')+'</aside>');
}
function confirmDialog(title, text, action, label){
  closeOverlays();
  document.body.insertAdjacentHTML('beforeend','<div class="scrim" onclick="closeOverlays()"></div><div class="dialog" role="alertdialog"><div class="card-pad"><h2>'+title+'</h2><p class="muted">'+text+'</p></div><div class="row" style="justify-content:flex-end;padding:12px 16px;border-top:1px solid var(--gray-200)"><button class="btn ghost" onclick="closeOverlays()">Cancelar</button><button class="btn danger" onclick="closeOverlays();'+action+'">'+label+'</button></div></div>');
}
function toast(msg, undo){
  let box = $('.toasts'); if(!box){ document.body.insertAdjacentHTML('beforeend','<div class="toasts" aria-live="polite"></div>'); box=$('.toasts'); }
  const t = document.createElement('div'); t.className='toast';
  t.innerHTML = ic('check')+'<span>'+msg+'</span>'+(undo?'<button>Deshacer</button>':'');
  if(undo) t.querySelector('button').onclick=()=>{undo(); t.remove();};
  box.appendChild(t); setTimeout(()=>t.remove(), 5000);
}

// ---------- command palette ----------
const PAL = [
  ['Acciones','Nuevo pedido','#/pos','plus','N'],['Acciones','Cuadre de hoy','#/cuadre','cash',''],['Acciones','Invitar cliente al portal','#/clientes','send',''],
  ['Ir a','Inicio','#/inicio','home','G I'],['Ir a','Producción','#/produccion','board','G P'],['Ir a','Pedidos','#/pedidos','list','G O'],['Ir a','Pedidos de clientes','#/solicitudes','inbox','G S'],['Ir a','Clientes','#/clientes','users','G C'],
  ['Pedidos','Pedido 4125 · CARLOS MEDINA','#/pedidos','file',''],['Placas','HMF922 · Pedido 4125','#/pedidos','tag',''],['Clientes','TRÁMITES DEL VALLE','#/clientes','users',''],
];
function palette(){
  closeOverlays();
  document.body.insertAdjacentHTML('beforeend','<div class="scrim" onclick="closeOverlays()"></div><div class="dialog palette" role="dialog" aria-label="Buscar"><input placeholder="Busca una placa, pedido, cliente o acción" autofocus><div class="res"></div><div class="row muted small" style="padding:8px 16px;border-top:1px solid var(--gray-200)"><span class="kbd">↑↓</span> moverse <span class="kbd">Enter</span> abrir <span class="kbd">Esc</span> cerrar</div></div>');
  const inp = $('.palette input'), res = $('.palette .res');
  const draw = q => { let last='', i=0; res.innerHTML = PAL.filter(p=>!q||(p[1]).toLowerCase().includes(q.toLowerCase())).map(p=>{ const g = p[0]!==last ? '<div class="group">'+p[0]+'</div>' : ''; last=p[0]; return g+'<div class="item'+(i++===0?' on':'')+'" onclick="location.hash=\''+p[2]+'\';closeOverlays()">'+ic(p[3])+'<span>'+p[1]+'</span>'+(p[4]?'<span class="kbd">'+p[4]+'</span>':'')+'</div>'; }).join('') || '<div class="empty"><b>Sin resultados</b>Prueba con el número de placa o de pedido.</div>'; };
  inp.oninput = () => draw(inp.value); draw(''); inp.focus();
}
document.addEventListener('keydown', e => {
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='k'){ e.preventDefault(); palette(); }
  if(e.key==='Escape') closeOverlays();
});

// ---------- shell ----------
const NAV = [
  ['',[['#/inicio','Inicio','home']]],
  ['Ventas',[['#/pos','Punto de venta','cart'],['#/pedidos','Pedidos','list'],['#/solicitudes','Pedidos de clientes','inbox',3],['#/clientes','Clientes','users']]],
  ['Producción',[['#/produccion','Tablero','board'],['#/inventario','Inventario','box']]],
  ['Revisión',[['#/declaraciones','Declaraciones','file',3],['#/duplicados','Posibles duplicados','copy',1]]],
  ['Administración',[['#/cuadre','Cuadre de caja','cash'],['#/precios','Precios','tag'],['#/usuarios','Usuarios y roles','shield'],['#/siigo','Envíos a Siigo','send',1]]],
];
function shell(active, html){
  const nav = NAV.map(([g,items])=>(g?'<div class="nav-group">'+g+'</div>':'')+'<nav class="nav">'+items.map(([h,l,i,c])=>'<a href="'+h+'" class="'+(h===active?'on':'')+'">'+ic(i)+'<span>'+l+'</span>'+(c?'<span class="count">'+c+'</span>':'')+'</a>').join('')+'</nav>').join('');
  return '<div class="shell"><aside class="sidebar"><div class="brand"><img src="assets/logo_ingse.png" alt="INGSE"><div><b>INGSE</b><span>Placas</span></div></div>'+nav+
  '<div class="me"><div class="avatar">LM</div><div style="min-width:0"><div style="color:#fff">Laura Muñoz</div><div class="small" style="color:#8A8A8A">Punto de venta</div></div><a href="#/login" class="btn ghost icon sm" style="margin-left:auto;color:#ccc;border-color:#3a3a3a" title="Cerrar sesión">'+ic('logout')+'</a></div></aside>'+
  '<div class="main"><div class="topbar"><div class="search-trigger" onclick="palette()" role="button" tabindex="0">'+ic('search')+'<span>Buscar placa, pedido o cliente</span><span style="flex:1"></span><span class="kbd">Ctrl K</span></div><span style="flex:1"></span><button class="btn ghost theme-toggle" title="Modo nocturno" aria-label="Cambiar a modo '+(localStorage.theme==='dark'?'claro':'nocturno')+'" onclick="localStorage.theme=localStorage.theme===\'dark\'?\'light\':\'dark\';render()">'+(localStorage.theme==='dark'?'☀':'☾')+'</button><a class="btn primary sm" href="#/pos">'+ic('plus')+'Nuevo pedido</a></div><div class="content">'+html+'</div></div></div>';
}
const ROUTES = {};
function render(){
  closeOverlays();
  const h = location.hash || '#/';
  const fn = ROUTES[h] || ROUTES['#/'];
  document.body.classList.toggle('dark', localStorage.theme==='dark' || (!localStorage.theme && matchMedia('(prefers-color-scheme: dark)').matches));
  document.getElementById('app').innerHTML = fn();
  (fn.after||(()=>{}))();
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', render);
