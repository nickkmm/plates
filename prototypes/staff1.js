// Staff screens, part 1
ROUTES['#/'] = () => '<div class="index"><div class="row" style="margin-bottom:6px"><img src="assets/logo_ingse.png" style="height:44px"><div><h1>Prototipos · Plataforma de placas INGSE</h1><div class="muted">Datos de ejemplo. Nada se guarda. Prueba <span class="kbd">Ctrl K</span> dentro de la app.</div></div><span style="flex:1"></span><button class="btn ghost theme-toggle" title="Modo nocturno" aria-label="Cambiar a modo '+(localStorage.theme==='dark'?'claro':'nocturno')+'" onclick="localStorage.theme=localStorage.theme===\'dark\'?\'light\':\'dark\';render()">'+(localStorage.theme==='dark'?'☀':'☾')+'</button></div>'+
 [['Equipo INGSE',[['#/login','Iniciar sesión','Acceso del equipo'],['#/inicio','Inicio','Resumen del día'],['#/pos','Punto de venta','Registrar un pedido'],['#/pedidos','Pedidos','Buscar, filtrar y ver el detalle'],['#/produccion','Tablero de producción','Avanzar pedidos por estado'],['#/solicitudes','Pedidos de clientes','Lo que llega del portal'],['#/clientes','Clientes','Datos y acceso al portal'],['#/declaraciones','Declaraciones','Revisar y aprobar'],['#/duplicados','Posibles duplicados','Resolver placas repetidas'],['#/inventario','Inventario','Láminas disponibles y alistamiento'],['#/precios','Precios','Tarifa por tipo de placa'],['#/cuadre','Cuadre de caja','Totales por forma de pago'],['#/usuarios','Usuarios y roles','Accesos del equipo'],['#/siigo','Envíos a Siigo','Traslados pendientes o con error']]],
  ['Clientes',[['#/portal','Portal: inicio','Pedido abierto y enviados'],['#/portal/nuevo','Portal: pedido abierto','Agregar placas durante la semana'],['#/portal/enviados','Portal: enviados','Historial y estado'],['#/portal/pedido','Portal: detalle','Seguimiento de un pedido enviado'],['#/publico/declaracion','Declaración juramentada','Formulario público en el celular']]]]
 .map(([g,t])=>'<h2 style="margin:24px 0 10px">'+g+'</h2><div class="tiles">'+t.map(([h,a,b])=>'<a class="tile" href="'+h+'"><b>'+a+'</b><span class="muted">'+b+'</span></a>').join('')+'</div>').join('')+'</div>';

ROUTES['#/login'] = () => '<div class="auth"><div class="art"><img src="assets/logo_ingse.png" style="height:64px;align-self:flex-start"><div class="slogan">Una buena señal para ti</div><span class="muted">INGSE · Señalización vial · Palmira</span></div><div class="form"><form style="width:340px" class="grid" onsubmit="event.preventDefault();location.hash=\'#/inicio\'"><div><h1>Iniciar sesión</h1></div><label class="field"><span>Usuario</span><input class="input" autofocus value="laura"></label><label class="field"><span>Contraseña</span><input class="input" type="password" value="••••••••"></label><button class="btn primary lg block">Entrar</button><div class="hint">¿Olvidaste la contraseña? Pídele a un administrador que la cambie.</div></form></div></div>';

// ---------- POS ----------
let lines = [{type:'CARRO PARTICULAR',plate:'HMF922',qty:2,reason:'DETERIORO'},{type:'MOTO PARTICULAR',plate:'KWD91G',qty:1,reason:'PÉRDIDA'}];
function lineRow(l,i){
  const dup = l.plate==='KWD91G';
  return '<div class="line"><div class="idx">'+(i+1)+'</div>'+
  '<label class="field"><select class="input" onchange="lines['+i+'].type=this.value;drawPos()">'+D.types.map(t=>'<option'+(t===l.type?' selected':'')+'>'+t+'</option>').join('')+'</select></label>'+
  '<label class="field"><input class="input mono'+(dup?' warn':'')+'" value="'+l.plate+'" placeholder="ABC123" oninput="this.value=this.value.toUpperCase().replace(/\\s/g,\'\')" aria-label="Placa">'+(dup?'<span class="hint warn">Ya está en el pedido <a href="#/pedidos">4127</a></span>':'')+'</label>'+
  '<label class="field"><select class="input"><option>DETERIORO</option><option'+(l.reason==='PÉRDIDA'?' selected':'')+'>PÉRDIDA</option><option>OTROS</option></select></label>'+
  '<label class="field"><input class="input num" type="number" min="1" max="2" value="'+l.qty+'" onchange="lines['+i+'].qty=+this.value;drawPos()" aria-label="Cantidad"></label>'+
  '<label class="field"><input class="input num" value="'+cop(D.prices[l.type]*l.qty)+'" aria-label="Valor"></label>'+
  '<button class="btn ghost icon" title="Quitar" onclick="lines.splice('+i+',1);drawPos()">'+ic('trash')+'</button></div>';
}
function drawPos(){
  $('#lines').innerHTML = '<div class="line small muted" style="padding-top:0"><div></div><div>Tipo</div><div>Placa</div><div>Motivo</div><div>Cant.</div><div>Valor</div><div></div></div>'+lines.map(lineRow).join('');
  const total = lines.reduce((s,l)=>s+D.prices[l.type]*l.qty,0), n = lines.reduce((s,l)=>s+l.qty,0);
  $('#sum-total').textContent = cop(total); $('#sum-n').textContent = n+' placa'+(n===1?'':'s');
}
ROUTES['#/pos'] = () => shell('#/pos',
 '<div class="page-head"><h1>Nuevo pedido</h1><span class="badge plain">Borrador guardado</span><span class="spacer"></span><button class="btn ghost sm">'+ic('upload')+'Cargar PDF o Excel</button></div>'+
 '<div class="pos"><div class="grid gap16">'+
   '<div class="card card-pad grid"><h2>Cliente</h2><div class="grid g3">'+
     '<label class="field" style="grid-column:span 2"><span>Cliente</span><div class="with-icon">'+ic('search')+'<input class="input" value="TRÁMITES DEL VALLE"></div><span class="hint">GESTOR · NIT 901.234.567-1 · 317 000 1122</span></label>'+
     '<label class="field"><span>N.º de pedido</span><input class="input" value="Se asigna al guardar" disabled style="background:var(--gray-100);color:var(--gray-500)"></label>'+
     '<label class="field"><span>Fecha</span><input class="input" type="date" value="2026-11-18"></label>'+
     '<label class="field"><span>Entrega</span><input class="input" type="date" value="2026-11-19"></label>'+
     '<label class="field"><span>Ciudad</span><input class="input" value="PALMIRA"></label></div></div>'+
   '<div class="card card-pad"><div class="row between" style="margin-bottom:6px"><h2>Placas</h2><span class="muted small"><span class="kbd">Alt N</span> agregar línea</span></div><div class="lines" id="lines"></div>'+
     '<button class="btn ghost sm" style="margin-top:10px" onclick="lines.push({type:\'CARRO PARTICULAR\',plate:\'\',qty:2,reason:\'DETERIORO\'});drawPos()">'+ic('plus')+'Agregar placa</button></div>'+
   '<div class="drop">'+ic('upload',22)+'<div><b style="color:var(--ink-900)">Arrastra aquí el pedido en PDF o Excel</b><div class="small">Llenamos el formulario por ti para que lo revises.</div></div></div>'+
   '<div class="card card-pad grid"><h2>Notas</h2><textarea class="input" placeholder="Opcional"></textarea></div>'+
 '</div>'+
 '<aside class="summary card card-pad grid"><div class="muted small">Total</div><div class="total" id="sum-total"></div><div class="muted" id="sum-n"></div><div class="divider"></div>'+
   '<label class="field"><span>Forma de pago</span><select class="input"><option>EFECTIVO</option><option selected>TRANSFERENCIA</option><option>CRÉDITO</option><option>TARJETA DÉBITO</option><option>TARJETA CRÉDITO</option></select></label>'+
   '<label class="field"><span>Cuenta que recibe</span><select class="input"><option>AHORROS 7716</option><option>CORRIENTE 9655</option><option>BANCO BOGOTÁ</option></select></label>'+
   '<div class="hint">Al guardar se crea el traslado en Siigo. La factura se hace desde el pedido.</div>'+
   '<div class="alert warn">'+ic('alert')+'<span>KWD91G ya está en otro pedido. Revísala antes de guardar.</span></div>'+
   '<button class="btn primary lg block" onclick="toast(\'Pedido 4128 guardado · traslado CC-100-4128 enviado a Siigo\');location.hash=\'#/produccion\'">Guardar pedido <span class="kbd" style="margin-left:6px">Ctrl Enter</span></button>'+
 '</aside></div>');
ROUTES['#/pos'].after = drawPos;

// ---------- Orders ----------
function siigoBlock(o){
  const tr = o.id===902 ? '<div class="row"><span class="badge red">Traslado sin enviar</span><span class="muted small">Siigo no respondió</span><button class="btn ghost sm" style="margin-left:auto" onclick="toast(\'Traslado CC-100-'+o.n+' enviado\')">Reintentar</button></div>'
    : '<div class="row"><span class="badge green">Traslado CC-100-'+o.n+'</span><span class="muted small">Enviado '+o.date+'</span></div>';
  const doc = o.pay==='EFECTIVO' ? 'recibo de caja RC-2' : 'factura electrónica FV-3';
  const bill = o.id===905 ? '<div class="row" style="margin-top:8px"><span class="badge green">FV-3-1882</span><span class="muted small">Factura enviada a la DIAN</span></div>'
    : '<div class="row" style="margin-top:8px"><span class="muted small">Sin '+doc+'</span><button class="btn primary sm" style="margin-left:auto" onclick="siigoPreview('+o.id+')">Facturar en Siigo</button></div>';
  return tr + bill;
}
function siigoPreview(id){
  const o = D.orders.find(x=>x.id===id), cash = o.pay==='EFECTIVO';
  sheet((cash?'Recibo de caja RC-2':'Factura electrónica FV-3')+' · pedido '+o.n,
   '<div class="alert info">'+ic('alert')+'<span>'+(cash?'Se registra el ingreso en caja en Siigo.':'Se envía a la DIAN. Después no se puede borrar.')+'</span></div>'+
   '<dl class="dl" style="margin-top:16px"><dt>Cliente</dt><dd>'+o.cust+' · NIT 901.234.567-1</dd><dt>Forma de pago</dt><dd>'+o.pay+'</dd><dt>Fecha</dt><dd>'+o.date+'</dd></dl>'+
   '<div class="table-wrap" style="margin-top:16px"><table class="dt"><thead><tr><th>Producto Siigo</th><th class="num">Cant.</th><th class="num">Valor</th></tr></thead><tbody><tr style="cursor:default"><td>000452 · '+o.type+'</td><td class="num">'+o.plates.length+'</td><td class="num">'+cop(o.total)+'</td></tr></tbody></table></div>'+
   '<div class="row between" style="margin-top:12px"><b>Total</b><b>'+cop(o.total)+'</b></div>',
   '<button class="btn ghost" onclick="closeOverlays()">Cancelar</button><button class="btn primary" onclick="closeOverlays();toast(\''+(cash?'Recibo RC-2-653':'Factura FV-3-1883')+' creado en Siigo\')">Crear en Siigo</button>');
}
function orderPanel(id){
  const o = D.orders.find(x=>x.id===id);
  sheet('Pedido '+o.n, '<div class="row wrap" style="margin-bottom:16px">'+statusBadge(o.status)+dueBadge(o.due)+'<span class="badge plain">'+o.src+'</span></div>'+
   '<dl class="dl"><dt>Cliente</dt><dd><b>'+o.cust+'</b></dd><dt>Fecha</dt><dd>'+o.date+'</dd><dt>Forma de pago</dt><dd>'+o.pay+'</dd><dt>Comprobante</dt><dd>FACTURA FV-3-1882</dd><dt>Total</dt><dd><b>'+cop(o.total)+'</b></dd></dl>'+
   '<div class="divider"></div><h3 style="margin-bottom:8px">Placas</h3><div class="table-wrap"><table class="dt"><thead><tr><th>Placa</th><th>Tipo</th><th class="num">Cant.</th><th class="num">Valor</th></tr></thead><tbody>'+o.plates.map(p=>'<tr><td>'+plate(p,o.type)+'</td><td>'+o.type+'</td><td class="num">'+(o.type.startsWith('CARRO')?2:1)+'</td><td class="num">'+cop(o.total/o.plates.length)+'</td></tr>').join('')+'</tbody></table></div>'+
   '<div class="divider"></div>'+deliveryBlock(o)+'<h3 style="margin-bottom:10px">Siigo</h3>'+siigoBlock(o)+'<div class="divider"></div><h3 style="margin-bottom:10px">Historial</h3><ul class="timeline"><li class="done">Creado por Laura Muñoz<div class="muted small">'+o.date+' · 9:12 a. m.</div></li><li class="'+(o.status!=='pending'?'done':'now')+'">En realización<div class="muted small">'+(o.status!=='pending'?'Andrés Gil · 10:40 a. m.':'Pendiente')+'</div></li><li class="'+(o.status==='ready'||o.status==='delivered'?'done':'')+'">Lista para entregar</li><li>Entregada</li></ul>',
   '<button class="btn ghost" onclick="confirmDialog(\'¿Eliminar el pedido '+o.n+'?\',\'Se borran sus '+o.plates.length+' placas y se recalcula el inventario. No se puede deshacer.\',\'toast(&quot;Pedido eliminado&quot;)\',\'Eliminar pedido\')">'+ic('trash')+'Eliminar</button><span style="flex:1"></span><button class="btn ghost">Editar</button>', o.cust);
}
ROUTES['#/pedidos'] = () => shell('#/pedidos',
 '<div class="page-head"><h1>Pedidos</h1><span class="spacer"></span><button class="btn ghost sm">'+ic('upload')+'Buscar por archivo</button><button class="btn ghost sm">'+ic('download')+'Exportar</button></div>'+
 '<div class="toolbar"><div class="with-icon" style="width:280px">'+ic('search')+'<input class="input" placeholder="Placa o n.º de pedido"></div>'+
  '<button class="chip">'+ic('calendar',14)+'Últimos 7 días</button><button class="chip">Cliente</button><button class="chip">Forma de pago</button><button class="chip on">Estado: activos</button><button class="chip" style="border-style:dashed">'+ic('plus',14)+'Filtro</button></div>'+
 '<div class="table-wrap"><table class="dt"><thead><tr><th>Pedido</th><th>Fecha</th><th>Cliente</th><th>Placas</th><th>Pago</th><th class="num">Total</th><th>Estado</th></tr></thead><tbody>'+
 D.orders.map(o=>'<tr onclick="orderPanel('+o.id+')"><td><b>'+o.n+'</b>'+(o.src==='Portal'?' <span class="badge yellow plain">Portal</span>':'')+'</td><td>'+o.date+'</td><td>'+o.cust+'</td><td><div class="row wrap" style="gap:4px">'+o.plates.slice(0,2).map(p=>plate(p,o.type)).join('')+(o.plates.length>2?'<span class="muted small">+'+(o.plates.length-2)+'</span>':'')+'</div></td><td>'+o.pay+'</td><td class="num">'+cop(o.total)+'</td><td>'+statusBadge(o.status)+'</td></tr>').join('')+
 '</tbody></table><div class="pager"><span>8 de 1.284 pedidos</span><span class="row"><button class="btn ghost sm">Anterior</button><button class="btn ghost sm">Siguiente</button></span></div></div>');

// ---------- Delivery with signature ----------
// Closing an order requires the recipient's signature, drawn on the tablet.
function deliveryBlock(o){
  if (o.status !== 'delivered') return '';
  const sig = o.sig || {name:'CARLOS MEDINA', doc:'CC 16.789.432', when:'18/11/2026 4:12 p. m.', by:'Laura Muñoz'};
  return '<h3 style="margin-bottom:10px">Entrega</h3><div class="grid g2" style="align-items:start"><dl class="dl"><dt>Recibió</dt><dd>'+sig.name+'</dd><dt>Documento</dt><dd>'+sig.doc+'</dd><dt>Fecha</dt><dd>'+sig.when+'</dd><dt>Entregó</dt><dd>'+sig.by+'</dd></dl>'+
    '<figure class="sig-view">'+(sig.img?'<img src="'+sig.img+'" alt="Firma de '+sig.name+'">':'<svg viewBox="0 0 300 110" aria-label="Firma de '+sig.name+'"><path d="M20 80 C40 20, 60 20, 70 70 S100 100, 120 50 S150 10, 165 70 S200 90, 215 45 S250 40, 280 65" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>')+'<figcaption class="muted small">Firma de quien recibe</figcaption></figure></div><div class="divider"></div>';
}
let sigDrawn = false;
function deliverSheet(id){
  const o = D.orders.find(x=>x.id===id); sigDrawn = false;
  sheet('Entregar pedido '+o.n,
   '<div class="row wrap" style="gap:4px">'+o.plates.map(p=>plate(p,o.type)).join('')+'<span class="muted small" style="margin-left:6px">'+o.cust+'</span></div>'+
   '<div class="grid g2" style="margin-top:16px"><label class="field"><span>Nombre de quien recibe</span><input id="rcv-name" class="input" value="'+(o.decl?o.cust:'')+'" oninput="sigCheck()"></label><label class="field"><span>Documento</span><input id="rcv-doc" class="input" inputmode="numeric" placeholder="Opcional"></label></div>'+
   '<div class="field" style="margin-top:12px"><span>Firma</span><div class="sig-pad"><canvas id="sig" aria-label="Recuadro para firmar"></canvas><span class="sig-line"></span><span class="sig-ph muted">Firma aquí con el dedo o el lápiz</span></div>'+
   '<div class="row between" style="margin-top:6px"><span class="muted small">Al firmar, recibe las placas a satisfacción.</span><button class="btn ghost sm" onclick="sigClear()">Borrar firma</button></div></div>',
   '<button class="btn ghost" onclick="closeOverlays()">Cancelar</button><button id="sig-ok" class="btn primary" disabled onclick="sigConfirm('+id+')">Confirmar entrega</button>');
  sigInit();
}
function sigInit(){
  const c = document.getElementById('sig'); if(!c) return;
  const r = c.getBoundingClientRect(), dpr = window.devicePixelRatio||1;
  c.width = r.width*dpr; c.height = r.height*dpr;
  const ctx = c.getContext('2d'); ctx.scale(dpr,dpr); ctx.lineWidth = 2.5; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--ink-900');
  let down = false;
  const pt = e => { const b = c.getBoundingClientRect(); return [e.clientX-b.left, e.clientY-b.top]; };
  c.onpointerdown = e => { down = true; c.setPointerCapture(e.pointerId); const [x,y] = pt(e); ctx.beginPath(); ctx.moveTo(x,y); };
  c.onpointermove = e => { if(!down) return; const [x,y] = pt(e); ctx.lineTo(x,y); ctx.stroke(); if(!sigDrawn){ sigDrawn = true; c.parentNode.classList.add('signed'); sigCheck(); } };
  c.onpointerup = c.onpointercancel = () => { down = false; };
}
function sigClear(){ const c = document.getElementById('sig'); c.getContext('2d').clearRect(0,0,c.width,c.height); sigDrawn = false; c.parentNode.classList.remove('signed'); sigCheck(); }
function sigCheck(){ const b = document.getElementById('sig-ok'); if(b) b.disabled = !(sigDrawn && document.getElementById('rcv-name').value.trim()); }
function sigConfirm(id){
  const o = D.orders.find(x=>x.id===id);
  o.sig = {name: document.getElementById('rcv-name').value.trim().toUpperCase(), doc: document.getElementById('rcv-doc').value || '—', when:'Hoy, ahora', by:'Laura Muñoz', img: document.getElementById('sig').toDataURL('image/png')};
  o.status = 'delivered'; closeOverlays(); render();
  toast('Pedido '+o.n+' entregado a '+o.sig.name, ()=>{ o.status='ready'; delete o.sig; render(); });
}

// ---------- Production board ----------
let boardView = 'board';
function card(o){
  const next = {pending:'Empezar',in_progress:'Marcar lista',ready:'Entregar'}[o.status];
  return '<div class="kcard" draggable="true" onclick="orderPanel('+o.id+')"><div class="top"><span class="ord">'+o.n+'</span>'+(o.decl?'<span class="badge plain" title="Tiene declaración">'+ic('file',12)+'Declaración</span>':'')+'<span style="flex:1"></span>'+dueBadge(o.due)+'</div><div>'+o.cust+'</div><div class="plates">'+o.plates.map(p=>plate(p,o.type)).join('')+'</div><div class="foot"><span class="muted small">'+o.type+'</span>'+(next?'<button class="btn sm" onclick="event.stopPropagation();advance('+o.id+')">'+next+'</button>':'')+'</div></div>';
}
function advance(id){
  const o = D.orders.find(x=>x.id===id), order = ['pending','in_progress','ready','delivered'], prev = o.status;
  if (prev === 'ready') return deliverSheet(id);
  o.status = order[order.indexOf(prev)+1]; render();
  toast('Pedido '+o.n+' → '+D.statuses[o.status][0], ()=>{ o.status=prev; render(); });
}
ROUTES['#/produccion'] = () => shell('#/produccion',
 '<div class="page-head"><h1>Producción</h1><span class="muted small">Actualizado hace 5 s</span><span class="spacer"></span><div class="with-icon" style="width:240px">'+ic('search')+'<input class="input" placeholder="Placa, pedido o cliente"></div><div class="seg"><button class="'+(boardView==='board'?'on':'')+'" onclick="boardView=\'board\';render()">Tablero</button><button class="'+(boardView==='list'?'on':'')+'" onclick="boardView=\'list\';render()">Lista</button></div></div>'+
 (boardView==='board' ? '<div class="board">'+Object.keys(D.statuses).map(s=>{ const os = D.orders.filter(o=>o.status===s); return '<section class="col"><div class="col-head">'+statusBadge(s)+(s==='delivered'?'<span class="muted small">Hoy</span>':'')+'<span class="n">'+os.length+'</span></div>'+(os.map(card).join('')||'<div class="empty small">Nada por aquí</div>')+'</section>'; }).join('')+'</div>'
 : '<div class="table-wrap"><table class="dt"><thead><tr><th>Pedido</th><th>Cliente</th><th>Placas</th><th>Entrega</th><th>Estado</th><th></th></tr></thead><tbody>'+D.orders.map(o=>'<tr onclick="orderPanel('+o.id+')"><td><b>'+o.n+'</b></td><td>'+o.cust+'</td><td>'+o.plates.map(p=>plate(p,o.type)).join(' ')+'</td><td>'+dueBadge(o.due)+'</td><td>'+statusBadge(o.status)+'</td><td class="num">'+(o.status!=='delivered'?'<button class="btn sm ghost" onclick="event.stopPropagation();advance('+o.id+')">Avanzar</button>':'')+'</td></tr>').join('')+'</tbody></table></div>'));

// ---------- Customer pedidos inbox (staff side) ----------
// Arrives like declarations: review, then "Crear pedido" prefills the point of sale; saving marks it attended.
let reqSel = 0;
ROUTES['#/solicitudes'] = () => { const r = D.requests[reqSel]; return shell('#/solicitudes',
 '<div class="page-head"><h1>Pedidos de clientes</h1><span class="spacer"></span><div class="seg"><button class="on">Por atender 3</button><button>Devueltos</button><button>Atendidos</button></div></div>'+
 '<div class="split"><div class="list">'+D.requests.map((q,i)=>'<div class="it'+(i===reqSel?' on':'')+'" onclick="reqSel='+i+';render()"><div class="row between"><b>'+q.cust+'</b><span class="muted small">'+q.when+'</span></div><div class="muted small">'+q.id+' · '+q.plates.length+(q.plates.length===1?' placa':' placas')+'</div><div class="row" style="margin-top:6px;gap:4px">'+q.plates.slice(0,3).map(p=>plate(p,q.type)).join('')+(q.plates.length>3?'<span class="muted small">+'+(q.plates.length-3)+'</span>':'')+'</div></div>').join('')+'</div>'+
 '<div class="detail grid gap16"><div class="row"><div><h2>'+r.id+' · '+r.cust+'</h2><div class="muted small">Enviado '+r.when.toLowerCase()+' por Diana Paredes</div></div><span style="flex:1"></span><button class="btn ghost" onclick="sheet(\'Devolver '+r.id+' para corregir\',\'<label class=&quot;field&quot;><span>Qué debe corregir el cliente</span><textarea class=&quot;input&quot; rows=&quot;4&quot;>KWD91G ya está en el pedido 4127.</textarea></label><div class=&quot;hint&quot; style=&quot;margin-top:8px&quot;>El pedido vuelve a su portal para que lo corrija y lo reenvíe.</div>\',\'<button class=&quot;btn ghost&quot; onclick=&quot;closeOverlays()&quot;>Cancelar</button><button class=&quot;btn&quot; onclick=&quot;closeOverlays();toast(&#39;Pedido devuelto al cliente&#39;)&quot;>Devolver</button>\')">Devolver para corregir</button><button class="btn primary" onclick="toast(\'Punto de venta con las placas de '+r.id+'\');location.hash=\'#/pos\'">Crear pedido</button></div>'+
 '<dl class="dl"><dt>Forma de pago</dt><dd>'+r.pay+'</dd></dl>'+
 '<div class="table-wrap"><table class="dt"><thead><tr><th>Placa</th><th>Tipo</th><th class="num">Cant.</th><th>Ciudad</th><th>Revisión</th></tr></thead><tbody>'+r.plates.map(p=>'<tr style="cursor:default"><td>'+plate(p,r.type)+'</td><td>'+r.type+'</td><td class="num">'+(r.type.startsWith('CARRO')?2:1)+'</td><td>PALMIRA</td><td>'+(p==='KWD91G'?'<span class="badge amber">Ya está en el pedido 4127</span>':'<span class="badge green">Nueva</span>')+'</td></tr>').join('')+'</tbody></table></div>'+
 '<div class="muted small">Valor estimado con la tarifa: <b>'+cop(D.prices[r.type]*r.plates.length)+'</b></div>'+
 '</div></div>'); };

// ---------- Customers ----------
const portalBadge = s => s==='activo'?'<span class="badge green">Portal activo</span>':s==='invitado'?'<span class="badge amber">Invitación enviada</span>':'<span class="badge plain">Sin acceso</span>';
function customerPanel(id){
  const c = D.customers.find(x=>x.id===id);
  sheet(c.name, '<div class="row" style="margin-bottom:16px"><span class="badge plain">'+c.type+'</span>'+portalBadge(c.portal)+'</div>'+
  '<div class="tabs"><a class="on">Datos</a><a>Pedidos ('+c.orders+')</a><a>Tarjetas de propiedad</a></div>'+
  '<div class="grid g2"><label class="field"><span>Nombre</span><input class="input" value="'+c.name+'"></label><label class="field"><span>Tipo</span><select class="input"><option>'+c.type+'</option></select></label><label class="field"><span>Documento</span><input class="input" value="'+c.doc+'"></label><label class="field"><span>Teléfono</span><input class="input" value="'+c.phone+'"></label><label class="field"><span>Municipio</span><input class="input" value="'+c.city+'"></label><label class="field"><span>Correo</span><input class="input" placeholder="Opcional"></label></div>'+
  '<div class="divider"></div><div class="card card-pad grid"><div class="row between"><h3>Acceso al portal</h3>'+portalBadge(c.portal)+'</div>'+
  (c.portal==='activo'?'<div class="muted small">2 usuarios · último ingreso hoy, 8:41 a. m.</div><div class="row"><button class="btn ghost sm">Ver usuarios</button><button class="btn ghost sm">'+ic('plus',14)+'Agregar usuario</button></div>'
   :c.portal==='invitado'?'<div class="muted small">Invitación enviada a gestora@ejemplo.co hace 2 días.</div><div class="row"><button class="btn ghost sm" onclick="toast(\'Invitación reenviada\')">Reenviar</button></div>'
   :'<div class="muted small">Con acceso, el cliente envía sus pedidos y ve su estado sin escribir por correo.</div><div class="row"><input class="input" placeholder="Correo del cliente" style="max-width:260px"><button class="btn primary sm" onclick="toast(\'Invitación enviada\')">'+ic('send',14)+'Invitar</button></div>')+'</div>',
  '<button class="btn ghost" onclick="closeOverlays()">Cancelar</button><button class="btn" onclick="closeOverlays();toast(\'Cliente actualizado\')">Guardar cambios</button>');
}
ROUTES['#/clientes'] = () => shell('#/clientes',
 '<div class="page-head"><h1>Clientes</h1><span class="spacer"></span><button class="btn primary sm">'+ic('plus')+'Nuevo cliente</button></div>'+
 '<div class="toolbar"><div class="with-icon" style="width:280px">'+ic('search')+'<input class="input" placeholder="Nombre o documento"></div><button class="chip on">Todos <span class="n">6</span></button><button class="chip">Empresas</button><button class="chip">Gestores</button><button class="chip">Con portal</button></div>'+
 '<div class="table-wrap"><table class="dt"><thead><tr><th>Cliente</th><th>Tipo</th><th>Documento</th><th>Teléfono</th><th class="num">Pedidos</th><th>Portal</th></tr></thead><tbody>'+
 D.customers.map(c=>'<tr onclick="customerPanel('+c.id+')"><td><b>'+c.name+'</b><div class="muted small">'+c.city+'</div></td><td>'+c.type+'</td><td>'+c.doc+'</td><td>'+c.phone+'</td><td class="num">'+c.orders+'</td><td>'+portalBadge(c.portal)+'</td></tr>').join('')+'</tbody></table></div>');
