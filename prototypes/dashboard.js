// ---------- Home dashboard (both directions) ----------
const DAYS = [['13/11',18],['14/11',22],['15/11',9],['16/11',4],['17/11',25],['18/11',27],['19/11',21],['20/11',19],['21/11',24],['22/11',11],['23/11',3],['24/11',26],['25/11',29],['26/11',23]];
function barChart(){
  const W=640,H=190,pl=36,pb=24,pt=18, max=30, bw=24, slot=(W-pl-8)/DAYS.length;
  const y = v => pt+(H-pt-pb)*(1-v/max);
  let s = '<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="Placas registradas por día, últimos 14 días">';
  [0,10,20,30].forEach(t=>{ s+='<line x1="'+pl+'" x2="'+(W-4)+'" y1="'+y(t)+'" y2="'+y(t)+'" style="stroke:var(--gray-200)" stroke-width="1"/><text x="'+(pl-8)+'" y="'+(y(t)+4)+'" font-size="11" text-anchor="end" style="fill:var(--gray-500)">'+t+'</text>'; });
  DAYS.forEach(([d,v],i)=>{
    const x = pl+slot*i+(slot-bw)/2, top=y(v), h=y(0)-top, r=Math.min(4,h);
    s += '<path d="M'+x+','+y(0)+' V'+(top+r)+' Q'+x+','+top+' '+(x+r)+','+top+' H'+(x+bw-r)+' Q'+(x+bw)+','+top+' '+(x+bw)+','+(top+r)+' V'+y(0)+' Z" style="fill:var(--ink-700)"/>';
    s += '<rect x="'+(pl+slot*i)+'" y="'+pt+'" width="'+slot+'" height="'+(H-pt-pb)+'" fill="transparent" data-d="'+d+'" data-v="'+v+'" onmousemove="chartTip(event,this)" onmouseleave="chartTip(null)"/>';
    if(i%2===1||i===DAYS.length-1) s += '<text x="'+(x+bw/2)+'" y="'+(H-6)+'" font-size="11" text-anchor="middle" style="fill:var(--gray-500)">'+d+'</text>';
    if(i===DAYS.length-1) s += '<text x="'+(x+bw/2)+'" y="'+(top-6)+'" font-size="12" font-weight="700" text-anchor="middle" style="fill:var(--ink-900)">'+v+'</text>';
  });
  return s+'</svg>';
}
function chartTip(e, el){
  const tip = $('.chart .tip'); if(!tip) return;
  if(!e){ tip.style.display='none'; return; }
  const box = $('.chart').getBoundingClientRect();
  tip.innerHTML = '<b>'+el.dataset.v+' placas</b> · '+el.dataset.d; tip.style.display='block';
  tip.style.left = (e.clientX-box.left)+'px'; tip.style.top = (e.clientY-box.top)+'px';
}
ROUTES['#/inicio'] = () => {
  const cnt = s => D.orders.filter(o=>o.status===s).length;
  const attn = [
    ['alert','var(--red-50)','var(--red)','Entregas vencidas',2,'#/produccion'],
    ['inbox','var(--yellow-50)','#7A5C00','Pedidos de clientes por atender',3,'#/solicitudes'],
    ['file','var(--blue-50)','var(--blue)','Declaraciones por revisar',3,'#/declaraciones'],
    ['copy','var(--amber-50)','var(--amber)','Posibles duplicados',1,'#/duplicados'],
    ['box','var(--red-50)','var(--red)','Lámina PÚBLICO baja: 18',null,'#/inventario'],
    ['send','var(--gray-100)','var(--gray-600)','Envíos a Siigo con error',1,'#/siigo'],
  ];
  return shell('#/inicio',
  '<div class="page-head"><div><h1>Buenos días, Laura</h1><div class="muted">Miércoles 26 de noviembre</div></div><span class="spacer"></span><a class="btn ghost sm" href="#/cuadre">'+ic('cash')+'Cuadre de hoy</a></div>'+
  '<div class="grid g4" style="margin-bottom:16px">'+
   [['Pedidos hoy','14','3 más que ayer'],['Placas por entregar','23','En 7 pedidos'],['Entregas vencidas','2','Revisar en producción',true],['Caja de hoy',cop(1834000),'14 pedidos']].map(([l,v,h,bad])=>'<div class="card kpi"><div class="label">'+l+'</div><div class="value"'+(bad?' style="color:var(--red)"':'')+'>'+v+'</div><div class="muted small" style="margin-top:2px">'+h+'</div></div>').join('')+'</div>'+
  '<div class="dash"><div class="grid gap16">'+
   '<div class="card"><div class="row between card-pad" style="padding-bottom:0"><h2>Producción</h2><a class="small" href="#/produccion">Abrir tablero</a></div><div class="pipeline" style="margin-top:8px">'+
     [['pending'],['in_progress'],['ready'],['delivered']].map(([s])=>'<a class="stage" href="#/produccion">'+statusBadge(s)+'<div class="n">'+(s==='delivered'?'1':cnt(s))+'</div><div class="muted small">'+(s==='delivered'?'hoy':'pedidos')+'</div></a>').join('')+'</div></div>'+
   '<div class="card card-pad"><div class="row between"><h2>Placas registradas</h2><span class="muted small">Últimos 14 días</span></div><div class="chart" style="margin-top:8px">'+barChart()+'<div class="tip"></div></div></div>'+
   '<div class="card card-pad"><h2 style="margin-bottom:6px">Actividad reciente</h2><ul class="feed">'+
     [['LM','Laura Muñoz guardó el pedido <b>4127</b>','hace 5 min'],['TV','TRÁMITES DEL VALLE envió <b>P-0231</b> (5 placas)','hace 12 min'],['AG','Andrés Gil marcó lista la <b>4122</b>','hace 18 min'],['AD','Admin cambió el precio de MOTO PARTICULAR','ayer']].map(([a,t,w])=>'<li><div class="avatar" style="width:26px;height:26px;font-size:11px">'+a+'</div><div style="flex:1">'+t+'</div><span class="muted small">'+w+'</span></li>').join('')+'</ul></div>'+
  '</div><div class="grid gap16">'+
   '<div class="card card-pad"><h2 style="margin-bottom:4px">Requiere atención</h2><div class="attn">'+attn.map(([i,bg,fg,t,n,h])=>'<a href="'+h+'"><span class="ico" style="background:'+bg+';color:'+fg+'">'+ic(i)+'</span><span>'+t+'</span>'+(n!==null?'<span class="n">'+n+'</span>':'<span class="n"></span>')+ic('arrow',14)+'</a>').join('')+'</div></div>'+
   '<div class="card card-pad grid"><div class="row between"><h2>Láminas</h2><a class="small" href="#/inventario">Inventario</a></div>'+
     [['CARRO PARTICULAR',412,1000,''],['MOTO PARTICULAR',96,800,'warn'],['PÚBLICO',18,300,'low']].map(([n,a,t,c])=>'<div><div class="row between small"><span>'+n+'</span><b>'+a+'</b></div><div class="bar '+c+'" style="margin-top:4px"><i style="width:'+Math.round(a/t*100)+'%"></i></div></div>').join('')+'</div>'+
  '</div></div>');
};
