(() => {
  const el = document.getElementById('mapid'), status = document.getElementById('mapstatus');
  if (!el || !status) return;
  const heatToggle = document.getElementById('map-heat');
  const filters = ['map-kind', 'map-filter-status', 'map-department', 'map-ward', 'map-search'].map(id => document.getElementById(id));
  let pins = [], map = null, group = null, heat = null, timer, pending;
  function link(pin) {
    return pin.kind === 'project' ? '/projects/' + pin.id : pin.kind === 'citizen_report' ? '/citizen-reports/' + pin.id : pin.kind === 'joint' ? '/joint-schedules/' + pin.id : '/coordination';
  }
  function draw() {
    if (!map) {
      el.replaceChildren();
      const list = document.createElement('ul');
      pins.forEach(pin => { const row = document.createElement('li'), a = document.createElement('a'); a.href = link(pin); a.textContent = pin.title; row.append(a); list.append(row); });
      el.append(list);
      status.textContent = 'Map library unavailable; showing ' + pins.length + ' mapped items as a list. Address-only reports are excluded.';
      return;
    }
    group.clearLayers();
    if (heat) { map.removeLayer(heat); heat = null; }
    pins.forEach(pin => {
      const box = document.createElement('div'), title = document.createElement('strong'), a = document.createElement('a');
      title.textContent = pin.title; a.href = link(pin); a.textContent = 'Details';
      box.append(title, document.createElement('br'), document.createTextNode(pin.badge || ''), document.createElement('br'), a);
      const color = ['green', 'blue', 'orange', 'purple'].includes(pin.color) ? pin.color : 'blue';
      // MarkerCluster expects markers, not path layers. A small validated-color
      // div icon retains the original public layer colors and clusters properly.
      const icon = L.divIcon({className: 'civic-map-marker', html: '<span class="pin ' + color + '"></span>', iconSize: [18, 18], iconAnchor: [9, 9]});
      L.marker([pin.lat, pin.lng], {icon}).bindPopup(box).addTo(group);
    });
    if (heatToggle.checked && L.heatLayer) heat = L.heatLayer(pins.map(pin => [pin.lat, pin.lng, .6]), {radius: 25}).addTo(map);
    status.textContent = pins.length + ' mapped items. Address-only reports are excluded.' + (L.markerClusterGroup ? '' : ' Clustering unavailable.') + (heatToggle.checked && !L.heatLayer ? ' Heatmap unavailable.' : '');
  }
  async function refresh() {
    if (pending) pending.abort();
    pending = new AbortController();
    const params = new URLSearchParams();
    filters.forEach(control => { if (control && control.value.trim()) params.set(control.name, control.value.trim()); });
    try {
      const response = await fetch(el.dataset.mapdataUrl + '?' + params.toString(), {signal: pending.signal});
      if (!response.ok) throw Error('HTTP ' + response.status);
      const data = await response.json();
      pins = (data.pins || []).filter(pin => Number.isFinite(pin.lat) && Number.isFinite(pin.lng) && Math.abs(pin.lat) <= 90 && Math.abs(pin.lng) <= 180);
      if (!map && typeof L !== 'undefined') {
        map = L.map(el).setView([20, 0], 2);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution: '© OpenStreetMap contributors'}).addTo(map).on('tileerror', () => { status.textContent = 'Map tiles unavailable; markers and filters remain available.'; });
        group = (L.markerClusterGroup ? L.markerClusterGroup() : L.layerGroup()).addTo(map);
        if (pins.length) map.fitBounds(pins.map(pin => [pin.lat, pin.lng]), {maxZoom: 15});
      }
      draw();
    } catch (error) { if (error.name !== 'AbortError') status.textContent = 'Map data unavailable. Please retry.'; }
  }
  filters.forEach(control => control && control.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(refresh, 180); }));
  heatToggle.addEventListener('change', draw);
  refresh();
})();
