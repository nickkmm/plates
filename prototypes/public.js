// Public declaration (mobile) and customer portal
let pstep = 0;
const PSTEPS = ['Tus datos','Vehículos','Fotos','Firma','Listo'];
function pubBody(){
  if(pstep===0) return '<div><h2>Tus datos</h2><div class="muted small">Paso 1 de 4</div></div>'+
    '<div class="field"><span>Actúas como</span><div class="grid" style="gap:8px">'+['Propietario','Poseedor','Gestor'].map((x,i)=>'<label class="choice-card'+(i===0?' on':'')+'"><input type="radio" name="as"'+(i===0?' checked':'')+'> '+x+'</label>').join('')+'</div></div>'+
    '<label class="field"><span>Nombre completo</span><input class="input" autocomplete="name"></label>'+
    '<div class="grid g2"><label class="field"><span>Documento</span><select class="input"><option>Cédula</option><option>NIT</option><option>C. extranjería</option></select></label><label class="field"><span>Número</span><input class="input" inputmode="numeric"></label></div>'+
    '<label class="field"><span>Celular</span><input class="input" inputmode="tel"></label>'+
    '<label class="field"><span>Correo <span class="muted">(opcional)</span></span><input class="input" inputmode="email"></label>';
  if(pstep===1) return '<div><h2>Vehículos</h2><div class="muted small">Paso 2 de 4 · Una tarjeta de propiedad por vehículo</div></div>'+
    '<div class="card card-pad grid"><div class="row between"><b>Vehículo 1</b><button class="btn ghost sm">Quitar</button></div><label class="field"><span>Placa</span><input class="input mono" value="HMF922"></label><div class="grid g2"><label class="field"><span>Tipo</span><select class="input"><option>Carro</option><option>Moto</option></select></label><label class="field"><span>Placas</span><select class="input"><option>2</option><option>1</option></select></label></div><div class="field"><span>Motivo</span><div class="seg"><button class="on">Deterioro</button><button>Pérdida</button></div></div></div>'+
    '<button class="btn ghost block">'+ic('plus')+'Agregar otro vehículo</button>';
  if(pstep===2) return '<div><h2>Fotos</h2><div class="muted small">Paso 3 de 4 · HMF922</div></div>'+
    ['Tarjeta de propiedad · frente','Tarjeta de propiedad · reverso','Placas que vas a entregar'].map((x,i)=>'<div class="cam">'+ic(i===0?'check':'camera',22)+'<div style="flex:1"><b style="color:var(--ink-900)">'+x+'</b><div class="small">'+(i===0?'Lista · 1,2 MB':'Toca para tomar la foto')+'</div></div>'+(i===0?'<span class="badge green">Lista</span>':'')+'</div>').join('')+
    '<label class="row small"><input type="checkbox"> Perdí mis placas</label>';
  if(pstep===3) return '<div><h2>Firma</h2><div class="muted small">Paso 4 de 4</div></div><div class="alert info">'+ic('file')+'<span>Declaro bajo juramento que soy propietario o poseedor del vehículo y autorizo a INGSE a fabricar el duplicado. <a href="#/publico/declaracion">Leer completo</a></span></div><div class="photo" style="aspect-ratio:1.8;background:#fff;border-style:dashed">Firma aquí con el dedo</div><button class="btn ghost sm" style="align-self:flex-start">Borrar firma</button>';
  return '<div style="text-align:center;padding:40px 0" class="grid"><div style="width:64px;height:64px;border-radius:50%;background:var(--green-50);color:var(--green);display:grid;place-items:center;margin:0 auto">'+ic('check',32)+'</div><h2>Declaración enviada</h2><div class="muted">Número <b>#313</b>. Te escribimos al celular cuando esté revisada.</div></div>';
}
ROUTES['#/publico/declaracion'] = () => '<div class="phone"><div class="public-top"><img src="assets/logo_ingse.png" alt="INGSE"><b>Declaración juramentada</b></div><div class="steps">'+PSTEPS.slice(0,4).map((_,i)=>'<i class="'+(i<=pstep?'on':'')+'"></i>').join('')+'</div><div class="pbody">'+pubBody()+'</div>'+
  (pstep<4?'<div class="pfoot">'+(pstep>0?'<button class="btn ghost lg" onclick="pstep--;render()">Atrás</button>':'')+'<button class="btn primary lg" style="flex:1" onclick="pstep++;render()">'+(pstep===3?'Enviar declaración':'Continuar')+'</button></div>':'<div class="pfoot"><button class="btn ghost lg block" onclick="pstep=0;render()">Hacer otra</button></div>')+'</div>';

// ---------- Customer portal ----------
// The customer keeps one open pedido, adds plates to it over the week, then sends it.
function portal(active, html){
  return '<div class="portal-top"><img src="assets/logo_ingse.png" alt="INGSE"><b>Portal de clientes</b><nav><a href="#/portal" class="'+(active==='home'?'on':'')+'">Inicio</a><a href="#/portal/nuevo" class="'+(active==='open'?'on':'')+'">Pedido abierto <span class="count" style="background:var(--yellow);color:var(--ink-900);border-radius:999px;padding:0 7px;font-size:11px;font-weight:700;margin-left:4px">'+openLines.reduce((s,l)=>s+l.qty,0)+'</span></a><a href="#/portal/enviados" class="'+(active==='sent'?'on':'')+'">Enviados</a></nav><span style="flex:1"></span><div class="row"><div class="avatar">TV</div><div><div>TRÁMITES DEL VALLE</div><div class="small" style="color:#999">Diana Paredes</div></div></div></div><div class="portal">'+html+'</div>';
}
let openLines = [
  {kind:'placa',type:'MOTO PARTICULAR',plate:'KWD91G',to:'',qty:1,city:'PALMIRA',added:'Lun 23'},
  {kind:'placa',type:'MOTO PARTICULAR',plate:'LMP22F',to:'',qty:1,city:'PALMIRA',added:'Lun 23'},
  {kind:'placa',type:'CARRO PARTICULAR',plate:'HTQ451',to:'',qty:2,city:'CALI',added:'Mar 24'},
  {kind:'rango',type:'MOTO PARTICULAR',plate:'ABC10D',to:'ABC14D',qty:5,city:'PALMIRA',added:'Mié 25'},
  {kind:'placa',type:'CARRO PÚBLICO',plate:'SPQ302',to:'',qty:2,city:'CANDELARIA',added:'Hoy'},
  {kind:'placa',type:'REMOLQUE',plate:'R54321',to:'',qty:1,city:'CANDELARIA',added:'Hoy'},
];
let addKind = 'placa';
const SENT = [
  ['P-0233','22/11/2026',4,'Devuelto para corregir','red','HTQ45D: el tipo es MOTO PARTICULAR, no CARRO.'],
  ['P-0231','21/11/2026',9,'Por revisar','amber',''],
  ['P-0228','14/11/2026',12,'En producción · pedido 4123','blue',''],
  ['P-0224','07/11/2026',7,'Listo para recoger · pedido 4118','green',''],
  ['P-0219','31/10/2026',15,'Entregado · pedido 4102','ink',''],
];
const sentBadge = s => '<span class="badge '+s[4]+'">'+s[3]+'</span>';

ROUTES['#/portal'] = () => { const n = openLines.reduce((s,l)=>s+l.qty,0); return portal('home',
 '<div class="page-head"><div><h1>Hola, Diana</h1><div class="muted">TRÁMITES DEL VALLE</div></div></div>'+
 '<div class="card card-pad row gap16" style="margin-bottom:20px;border-color:var(--yellow);box-shadow:0 0 0 3px var(--yellow-50)"><div style="flex:1"><div class="muted small">Pedido abierto</div><div style="font-size:22px;font-weight:700;color:var(--ink-900)">'+n+' placas</div><div class="muted small">Última placa agregada hoy · Sin enviar</div></div><a class="btn ghost" href="#/portal/nuevo">'+ic('plus')+'Agregar placas</a><button class="btn primary" onclick="sendOpen()">'+ic('send')+'Enviar a INGSE</button></div>'+
 '<div class="alert err" style="margin-bottom:16px">'+ic('alert')+'<span>INGSE te devolvió el <b>P-0233</b> para corregir. <a href="#/portal/nuevo">Corregir y reenviar</a></span></div>'+'<div class="row between" style="margin-bottom:10px"><h2>Enviados recientemente</h2><a href="#/portal/enviados" class="small">Ver todos</a></div>'+sentTable(SENT.slice(0,4))); };

function sentTable(rows){
  return '<div class="table-wrap"><table class="dt"><thead><tr><th>Pedido</th><th>Enviado</th><th class="num">Placas</th><th>Estado</th><th></th></tr></thead><tbody>'+
  rows.map(o=>'<tr onclick="location.hash=\''+(o[3].startsWith('Devuelto')?'#/portal/nuevo':'#/portal/pedido')+'\'"><td><b>'+o[0]+'</b></td><td>'+o[1]+'</td><td class="num">'+o[2]+'</td><td>'+sentBadge(o)+(o[5]?'<div class="hint err" style="margin-top:4px">INGSE: '+o[5]+'</div>':'')+'</td><td class="num">'+ic('arrow')+'</td></tr>').join('')+'</tbody></table></div>';
}
ROUTES['#/portal/enviados'] = () => portal('sent','<div class="page-head"><h1>Enviados</h1><span class="spacer"></span><div class="with-icon" style="width:260px">'+ic('search')+'<input class="input" placeholder="Buscar placa"></div></div>'+sentTable(SENT));

function sendOpen(){
  const n = openLines.reduce((s,l)=>s+l.qty,0);
  closeOverlays();
  document.body.insertAdjacentHTML('beforeend','<div class="scrim" onclick="closeOverlays()"></div><div class="dialog" role="alertdialog"><div class="card-pad grid"><h2>¿Enviar el pedido a INGSE?</h2><div class="muted">Vas a enviar '+n+' placas. Después de enviarlo ya no puedes cambiarlo; las placas nuevas irán a un pedido nuevo.</div><label class="field"><span>Forma de pago</span><select class="input"><option value="">Elige una</option><option>EFECTIVO</option><option>TRANSFERENCIA</option><option>CRÉDITO</option><option>TARJETA DÉBITO</option><option>TARJETA CRÉDITO</option></select></label></div><div class="row" style="justify-content:flex-end;padding:12px 16px;border-top:1px solid var(--gray-200)"><button class="btn ghost" onclick="closeOverlays()">Seguir agregando</button><button class="btn primary" onclick="closeOverlays();openLines=[];toast(\'Pedido P-0232 enviado\');location.hash=\'#/portal/pedido\'">Enviar</button></div></div>');
}
function lineLabel(l){ return l.kind==='rango' ? plate(l.plate,l.type)+' <span class="muted">a</span> '+plate(l.to,l.type) : plate(l.plate,l.type); }
function addLine(){
  const t = $('#add-type').value, p = $('#add-plate').value.trim().toUpperCase(), to = addKind==='rango' ? $('#add-to').value.trim().toUpperCase() : '';
  if(!p){ $('#add-plate').classList.add('err'); return; }
  openLines.unshift({kind:addKind,type:t,plate:p,to,qty:+$('#add-qty').value||1,city:$('#add-city').value.toUpperCase(),added:'Hoy'});
  render(); toast('Placa '+p+' agregada', ()=>{ openLines.shift(); render(); });
  setTimeout(()=>$('#add-plate') && $('#add-plate').focus(), 0);
}
ROUTES['#/portal/nuevo'] = () => { const n = openLines.reduce((s,l)=>s+l.qty,0); const byType = {}; openLines.forEach(l=>byType[l.type]=(byType[l.type]||0)+l.qty);
 return portal('open',
 '<div class="page-head"><div><h1>Pedido abierto</h1><div class="muted small">Se guarda solo. Agrega placas cuando quieras y envíalo cuando esté completo.</div></div><span class="spacer"></span><button class="btn ghost sm">'+ic('upload')+'Importar Excel</button></div>'+
 '<div class="pos"><div class="grid gap16">'+
  '<div class="card card-pad grid"><div class="row between"><h2>Agregar placa</h2><div class="seg"><button class="'+(addKind==='placa'?'on':'')+'" onclick="addKind=\'placa\';render()">Una placa</button><button class="'+(addKind==='rango'?'on':'')+'" onclick="addKind=\'rango\';render()">Rango</button></div></div>'+
   '<div class="grid" style="grid-template-columns:1.9fr 1fr '+(addKind==='rango'?'1fr ':'')+'.6fr 1fr auto;align-items:end">'+
   '<label class="field"><span>Tipo</span><select class="input" id="add-type">'+D.types.map(t=>'<option>'+t+'</option>').join('')+'</select></label>'+
   '<label class="field"><span>'+(addKind==='rango'?'Desde':'Placa')+'</span><input class="input mono" id="add-plate" placeholder="ABC123" autofocus oninput="this.value=this.value.toUpperCase()" onkeydown="if(event.key===\'Enter\')addLine()"></label>'+
   (addKind==='rango'?'<label class="field"><span>Hasta</span><input class="input mono" id="add-to" placeholder="ABC130" oninput="this.value=this.value.toUpperCase()"></label>':'')+
   '<label class="field"><span>Cant.</span><input class="input num" id="add-qty" type="number" min="1" value="1"></label>'+
   '<label class="field"><span>Ciudad</span><input class="input" id="add-city" value="PALMIRA"></label>'+
   '<button class="btn" onclick="addLine()">'+ic('plus')+'Agregar <span class="kbd" style="margin-left:4px">Enter</span></button></div></div>'+
  '<div class="table-wrap"><table class="dt"><thead><tr><th>Placa</th><th>Tipo</th><th class="num">Cant.</th><th>Ciudad</th><th>Agregada</th><th></th></tr></thead><tbody>'+
   (openLines.map((l,i)=>'<tr style="cursor:default"><td>'+lineLabel(l)+(l.plate==='KWD91G'?'<div class="hint warn" style="margin-top:4px">Ya la enviaste en el P-0231</div>':'')+'</td><td>'+l.type+'</td><td class="num">'+l.qty+'</td><td>'+l.city+'</td><td class="muted small">'+l.added+'</td><td class="num"><button class="btn ghost icon sm" title="Quitar" onclick="const x=openLines.splice('+i+',1)[0];render();toast(\'Placa quitada\',()=>{openLines.splice('+i+',0,x);render()})">'+ic('trash',14)+'</button></td></tr>').join('') || '<tr><td colspan="6"><div class="empty"><b>Sin placas todavía</b>Agrega la primera arriba o importa tu Excel.</div></td></tr>')+
  '</tbody></table></div>'+
   '</div>'+
 '<aside class="summary card card-pad grid"><div class="muted small">Pedido abierto</div><div class="total">'+n+' placas</div><div class="divider"></div>'+Object.entries(byType).map(([t,q])=>'<div class="row between small"><span>'+t+'</span><b>'+q+'</b></div>').join('')+'<div class="divider"></div><button class="btn primary lg block" '+(n?'':'disabled')+' onclick="sendOpen()">'+ic('send')+'Enviar a INGSE</button><div class="hint">Solo tú ves este pedido hasta que lo envíes.</div></aside></div>'); };

ROUTES['#/portal/pedido'] = () => portal('sent',
 '<div class="page-head"><a class="btn ghost icon" href="#/portal/enviados" aria-label="Volver">'+ic('back')+'</a><div><h1>Pedido P-0228</h1><div class="muted">Enviado el 14/11/2026 por Diana Paredes · TRANSFERENCIA</div></div></div>'+
 '<div class="card card-pad" style="margin-bottom:16px"><div class="stepper">'+['Enviado','Recibido · pedido 4123','En producción','Listo','Entregado'].map((s,i)=>(i?'<div class="ln'+(i<=2?' done':'')+'"></div>':'')+'<div class="s '+(i<2?'done':i===2?'now':'')+'"><b>'+(i<2?'✓':i+1)+'</b>'+s+'</div>').join('')+'</div></div>'+
 '<div class="grid g2"><div class="table-wrap"><table class="dt"><thead><tr><th>Placa</th><th>Tipo</th><th class="num">Cant.</th><th>Ciudad</th></tr></thead><tbody>'+[['JTR418','CARRO PARTICULAR',2,'PALMIRA'],['JTR419','CARRO PARTICULAR',2,'PALMIRA'],['JTR420','CARRO PARTICULAR',2,'CALI'],['QWE12B','MOTO PARTICULAR',1,'PALMIRA']].map(r=>'<tr style="cursor:default"><td>'+plate(r[0],r[1])+'</td><td>'+r[1]+'</td><td class="num">'+r[2]+'</td><td>'+r[3]+'</td></tr>').join('')+'</tbody></table></div>'+
 '<div class="card card-pad"><h2 style="margin-bottom:10px">Actividad</h2><ul class="timeline"><li class="done">Enviado<div class="muted small">14/11 · 5:02 p. m. · Diana Paredes</div></li><li class="done">Recibido por INGSE · pedido 4123<div class="muted small">15/11 · 8:15 a. m.</div></li><li class="now">En producción<div class="muted small">17/11 · 7:45 a. m.</div></li><li>Listo para recoger</li><li>Entregado</li></ul></div></div>');
