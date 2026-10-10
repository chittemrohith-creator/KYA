(() => {
 const lat=document.getElementById('report-lat'),lon=document.getElementById('report-lon'),status=document.getElementById('report-map-status'); let marker=null,map=null;
 document.getElementById('clear-report-location').onclick=()=>{lat.value='';lon.value='';if(marker){map.removeLayer(marker);marker=null;}status.textContent='Address-only report: not included on the map.';};
 if(typeof L==='undefined')return;
 map=L.map('report-map').setView([20,0],2);
 L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors'}).addTo(map).on('tileerror',()=>{status.textContent='Map tiles unavailable. Enter an address, or use a visible map location.';});
 function choose(pos){lat.value=pos.lat;lon.value=pos.lng;if(!marker){marker=L.marker(pos,{draggable:true}).addTo(map);marker.on('dragend',()=>choose(marker.getLatLng()));}else marker.setLatLng(pos);status.textContent='Map location selected. Drag the marker to adjust it.';}
 map.on('click',e=>choose(e.latlng));
 if(lat.value!==''&&lon.value!==''&&Number.isFinite(Number(lat.value))&&Number.isFinite(Number(lon.value))){choose(L.latLng(Number(lat.value),Number(lon.value)));map.setView(marker.getLatLng(),16);}
 else fetch('/api/mapdata').then(r=>r.json()).then(data=>{const pts=data.pins.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lng));if(pts.length)map.fitBounds(pts.map(p=>[p.lat,p.lng]),{maxZoom:15});}).catch(()=>{});
})();