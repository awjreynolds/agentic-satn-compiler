(function () {
  const loader = document.currentScript;
  const contextUrl = loader && loader.dataset.contextUrl;
  if (!contextUrl) return;

  const root = document.documentElement;
  const detail = document.getElementById('native-feature-details');
  const legend = document.querySelector('.native-panel .key');
  const routeLayer = 'native-bus-route';
  const routeCasingLayer = 'native-bus-route-casing';
  const interchangeLayer = 'native-bus-interchange';
  let started = false;
  let activePopup = null;

  const valueText = value => {
    if (typeof value === 'string') return value;
    if (value === undefined) return '';
    try { return JSON.stringify(value); }
    catch (_) { return String(value); }
  };
  const propertyRows = value => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value).filter(([key]) => key !== 'kind');
  };
  const firstValue = (value, keys) => {
    for (const key of keys) {
      const candidate = value && value[key];
      if (candidate !== undefined && candidate !== null &&
          !(typeof candidate === 'string' && !candidate.trim())) return candidate;
    }
    return undefined;
  };
  const collectionProvenance = context => {
    const values = {};
    ['metadata', 'provenance', 'properties'].forEach(key => {
      const entry = context[key];
      if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
        Object.assign(values, entry);
      }
    });
    ['attribution', 'licence', 'license', 'service_date'].forEach(key => {
      if (context[key] !== undefined) values[key] = context[key];
    });
    return values;
  };
  const appendRows = (parent, rows) => {
    if (!rows.length) return;
    const list = document.createElement('dl');
    rows.forEach(([label, value]) => {
      const term = document.createElement('dt');
      term.textContent = label.replaceAll('_', ' ');
      const description = document.createElement('dd');
      description.textContent = valueText(value);
      list.append(term, description);
    });
    parent.append(list);
  };
  const renderFeature = (feature, context) => {
    const properties = feature && feature.properties ? feature.properties : {};
    const isRoute = properties.kind === 'bus-route';
    const provenance = collectionProvenance(context);
    const content = document.createElement('div');
    const heading = document.createElement('h3');
    heading.textContent = isRoute ? 'Bus route service evidence' : 'Bus station or stop group';
    content.append(heading);
    const note = document.createElement('p');
    note.textContent = isRoute
      ? 'Route identifiers and shape are source evidence for the stated service date; they do not establish observed transfers.'
      : 'This is a source-labelled facility record; it does not establish an observed transfer connection.';
    content.append(note);
    const sourceRows = [
      ['Source', firstValue(properties, ['source_title', 'source_name', 'source_label', 'source_id']) ||
        firstValue(provenance, ['source_title', 'dataset_title', 'source_name', 'source_id'])],
      ['Source date', firstValue(provenance, [
        'source_date', 'dataset_date', 'effective_date', 'service_date'
      ])],
      ['Licence', firstValue(provenance, ['licence', 'license'])],
      ['Attribution', firstValue(provenance, ['attribution', 'copyright'])]
    ].filter(([, value]) => value !== undefined);
    const featureRows = isRoute
      ? [
          ['Route name', firstValue(properties, ['route_short_names', 'route_short_name', 'route_name'])],
          ['Route ID', firstValue(properties, ['route_ids', 'route_id'])],
          ['Service date', properties.service_date],
          ['Source ID', properties.source_id]
        ]
      : [
          ['Facility name', firstValue(properties, ['name', 'facility_name'])],
          ['Facility type', firstValue(properties, ['facility_type', 'interchange_type', 'type'])],
          ['Source ID', properties.source_id]
        ];
    if (sourceRows.length || featureRows.some(([, value]) => value !== undefined)) {
      const evidenceHeading = document.createElement('h4');
      evidenceHeading.textContent = isRoute ? 'Service evidence' : 'Facility evidence';
      content.append(evidenceHeading);
      appendRows(content, [...featureRows, ...sourceRows].filter(([, value]) => value !== undefined));
    }
    const fullSourceDetails = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'All source properties';
    fullSourceDetails.append(summary);
    appendRows(fullSourceDetails, propertyRows(properties));
    const collectionRows = propertyRows(provenance);
    if (collectionRows.length) {
      const datasetHeading = document.createElement('h4');
      datasetHeading.textContent = 'Dataset provenance';
      fullSourceDetails.append(datasetHeading);
      appendRows(fullSourceDetails, collectionRows);
    }
    if (propertyRows(properties).length || collectionRows.length) content.append(fullSourceDetails);
    return content;
  };
  const showFeature = (feature, context) => {
    if (!detail) return;
    detail.replaceChildren(...renderFeature(feature, context).childNodes);
  };
  const addControl = (kind, label, count, layers) => {
    const item = document.createElement('li');
    const control = document.createElement('label');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.dataset.busContextToggle = kind;
    toggle.checked = false;
    toggle.setAttribute('aria-label', 'Show ' + label.toLowerCase());
    const swatch = document.createElement('span');
    swatch.className = kind === 'routes'
      ? 'swatch bus-route-key'
      : 'marker-key marker-star-key bus-interchange-key';
    const text = document.createTextNode(label + ' (' + count + (kind === 'routes'
      ? ' mapped segments, long-dashed line)' : ' source facility records, star marker)'));
    control.append(toggle, document.createTextNode(' '), swatch, text);
    item.append(control);
    legend.append(item);
    toggle.addEventListener('change', () => {
      layers.forEach(layer => {
        if (window.SATN_NATIVE_MAP.getLayer(layer)) {
          window.SATN_NATIVE_MAP.setLayoutProperty(
            layer,
            'visibility',
            toggle.checked ? 'visible' : 'none'
          );
        }
      });
    });
  };
  const appendFailure = error => {
    root.dataset.nativeBusContextLoaded = 'false';
    root.dataset.nativeBusContextError = error.message || String(error);
    const failure = document.getElementById('native-loading-failure');
    if (failure) {
      failure.hidden = false;
      failure.textContent = 'Unable to load public bus context: ' + (error.message || error);
    }
  };
  const loadContext = async () => {
    const map = window.SATN_NATIVE_MAP;
    const response = await fetch(contextUrl);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const context = await response.json();
    if (!context || context.type !== 'FeatureCollection' || !Array.isArray(context.features)) {
      throw new Error('bus context is not a FeatureCollection');
    }
    const routes = context.features.filter(feature => feature.properties?.kind === 'bus-route');
    const interchanges = context.features.filter(
      feature => feature.properties?.kind === 'bus-interchange'
    );
    window.SATN_BUS_CONTEXT = context;
    if (routes.length || interchanges.length) {
      map.addSource('native-bus-context', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [...routes, ...interchanges] }
      });
      if (routes.length) {
        map.addLayer({
          id: routeCasingLayer,
          type: 'line',
          source: 'native-bus-context',
          filter: ['==', ['get', 'kind'], 'bus-route'],
          layout: { visibility: 'none', 'line-cap': 'round' },
          paint: { 'line-color': '#f8fafc', 'line-width': 6, 'line-dasharray': [3.5, 1.75] }
        });
        map.addLayer({
          id: routeLayer,
          type: 'line',
          source: 'native-bus-context',
          filter: ['==', ['get', 'kind'], 'bus-route'],
          layout: { visibility: 'none', 'line-cap': 'round' },
          paint: { 'line-color': '#cc79a7', 'line-width': 3.5, 'line-dasharray': [6, 3] }
        });
        addControl('routes', 'Bus route segments', routes.length, [routeCasingLayer, routeLayer]);
      }
      if (interchanges.length) {
        const markerSize = 28;
        const marker = document.createElement('canvas');
        marker.width = marker.height = markerSize;
        const markerContext = marker.getContext('2d');
        const center = markerSize / 2;
        markerContext.beginPath();
        for (let index = 0; index < 10; index += 1) {
          const radius = index % 2 === 0 ? 11 : 5;
          const angle = -Math.PI / 2 + (Math.PI * index) / 5;
          const x = center + Math.cos(angle) * radius;
          const y = center + Math.sin(angle) * radius;
          if (index === 0) markerContext.moveTo(x, y);
          else markerContext.lineTo(x, y);
        }
        markerContext.closePath();
        markerContext.fillStyle = '#d55e00';
        markerContext.strokeStyle = '#263238';
        markerContext.lineWidth = 2;
        markerContext.fill();
        markerContext.stroke();
        map.addImage(
          'native-bus-interchange-star',
          markerContext.getImageData(0, 0, markerSize, markerSize),
          { pixelRatio: 2 }
        );
        map.addLayer({
          id: interchangeLayer,
          type: 'symbol',
          source: 'native-bus-context',
          filter: ['==', ['get', 'kind'], 'bus-interchange'],
          layout: {
            visibility: 'none',
            'icon-image': 'native-bus-interchange-star',
            'icon-size': 1,
            'icon-allow-overlap': true,
            'icon-ignore-placement': true
          }
        });
        addControl(
          'interchanges',
          'Bus stations and stop groups',
          interchanges.length,
          [interchangeLayer]
        );
      }
      const interactiveLayers = [
        routes.length ? routeLayer : null,
        interchanges.length ? interchangeLayer : null
      ].filter(Boolean);
      map.on('mousemove', event => {
        map.getCanvas().style.cursor = map.queryRenderedFeatures(event.point, {
          layers: interactiveLayers
        }).length ? 'pointer' : '';
      });
      map.on('click', event => {
        const feature = map.queryRenderedFeatures(event.point, { layers: interactiveLayers })[0];
        if (!feature) return;
        showFeature(feature, context);
        if (activePopup) activePopup.remove();
        activePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true })
          .setLngLat(event.lngLat)
          .setDOMContent(renderFeature(feature, context))
          .addTo(map);
      });
    }
    root.dataset.nativeBusContextRoutes = String(routes.length);
    root.dataset.nativeBusContextInterchanges = String(interchanges.length);
    root.dataset.nativeBusContextLoaded = 'true';
  };
  const startWhenMapReady = () => {
    if (started || root.dataset.nativeReady !== 'true' || !window.SATN_NATIVE_MAP) return;
    started = true;
    loadContext().catch(appendFailure);
  };
  new MutationObserver(startWhenMapReady).observe(root, {
    attributes: true,
    attributeFilter: ['data-native-ready']
  });
  startWhenMapReady();
}());
