(function () {
  const loader = document.currentScript;
  const contextUrl = loader && loader.dataset.contextUrl;
  if (!contextUrl) return;

  const root = document.documentElement;
  const legend = document.querySelector('.native-panel .key');
  const routeLayer = 'native-bus-route';
  const routeCasingLayer = 'native-bus-route-casing';
  const interchangeLayer = 'native-bus-interchange';
  let started = false;

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
  const propertyArray = value => {
    if (Array.isArray(value)) return value;
    if (typeof value !== 'string') return [];
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  };
  const readableScheduleTypes = (value, action) => {
    const descriptions = {
      '0': `regular scheduled ${action}`,
      '1': `${action} is not available`,
      '2': `${action} by prior phone arrangement`,
      '3': `${action} by arrangement with the driver`
    };
    const codes = [...new Set(propertyArray(value).map(code => String(code)))];
    if (!codes.length) return 'Not reported';
    const labels = codes.map(code => descriptions[code] || `GTFS code ${code}`);
    return labels.length > 1 ? `Varies by service pattern: ${labels.join('; ')}` : labels[0];
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
    const isTransferCandidate = properties.transfer_candidate === true;
    const provenance = collectionProvenance(context);
    const content = document.createElement('div');
    const heading = document.createElement('h3');
    heading.textContent = isRoute
      ? 'Bus route service evidence'
      : isTransferCandidate
        ? 'Timetable-supported transfer candidate'
        : 'Bus station or stop group';
    content.append(heading);
    const note = document.createElement('p');
    note.textContent = isRoute
      ? 'Route identifiers and shape are source evidence for the stated service date; they do not establish observed transfers.'
      : isTransferCandidate
        ? 'Services share this selected stop on the stated date; interchange designation and connection timing are not verified.'
        : 'This is a source-labelled facility record; it does not establish an observed transfer connection.';
    content.append(note);
    const sourceRows = [
      ['Source', firstValue(properties, ['source_title', 'source_name', 'source_label', 'source_id']) ||
        firstValue(provenance, ['source_title', 'dataset_title', 'source_name', 'source_id'])],
      ['Source date', isTransferCandidate
        ? properties.source_creation_date_time
        : firstValue(provenance, [
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
      : isTransferCandidate
        ? [
            ['Stop', properties.name],
            ['Locality', properties.locality],
            ['Service date', properties.service_date],
            ['Routes', propertyArray(properties.route_short_names).join(', ')],
            ['Boarding', readableScheduleTypes(properties.pickup_type_codes, 'boarding')],
            ['Alighting', readableScheduleTypes(properties.drop_off_type_codes, 'alighting')],
            ['Evidence basis', properties.transfer_candidate_basis]
          ]
        : [
          ['Facility name', firstValue(properties, ['name', 'facility_name'])],
          ['Facility type', firstValue(properties, ['facility_type', 'interchange_type', 'type'])],
          ['Source ID', properties.source_id]
        ];
    if (sourceRows.length || featureRows.some(([, value]) => value !== undefined)) {
      const evidenceHeading = document.createElement('h4');
      evidenceHeading.textContent = isRoute
        ? 'Service evidence'
        : isTransferCandidate
          ? 'Transfer evidence'
          : 'Facility evidence';
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
  const addControl = (kind, label, count, layers, description) => {
    const item = document.createElement('li');
    item.className = 'layer-control-row';
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
    const text = document.createTextNode(`${label} (${count})`);
    control.append(toggle, document.createTextNode(' '), swatch, text);
    const help = document.createElement('details');
    help.className = 'layer-help';
    help.name = 'native-layer-help';
    const summary = document.createElement('summary');
    summary.setAttribute('aria-label', 'About ' + label.toLowerCase());
    summary.setAttribute('aria-describedby', 'layer-help-bus-' + kind);
    summary.textContent = 'ⓘ';
    help.append(summary);
    const helpText = document.createElement('span');
    helpText.id = 'layer-help-bus-' + kind;
    helpText.className = 'layer-help-popup';
    helpText.setAttribute('role', 'tooltip');
    helpText.textContent = description;
    item.append(control, help, helpText);
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
      if (!toggle.checked) {
        window.SATN_NATIVE_CLEAR_INSPECTION_IF_LAYER_HIDDEN?.(layers);
      }
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
    const sourceFacilities = interchanges.filter(
      feature => feature.properties?.transfer_candidate !== true
    );
    const transferCandidates = interchanges.filter(
      feature => feature.properties?.transfer_candidate === true
    );
    window.SATN_BUS_CONTEXT = context;
    window.SATN_NATIVE_BUS_POPUP_CONTENT = feature => renderFeature(feature, context);
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
        addControl(
          'routes',
          'Bus route segments',
          routes.length,
          [routeCasingLayer, routeLayer],
          'Source route geometry for the stated service date; it does not establish observed transfers or service reliability.'
        );
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
          'Bus facilities and transfer points',
          interchanges.length,
          [interchangeLayer],
          `Facility records: ${sourceFacilities.length}. Timetable-supported transfer candidates: ${transferCandidates.length}. Shared-stop evidence does not confirm a practical interchange or connection time.`
        );
      }
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
