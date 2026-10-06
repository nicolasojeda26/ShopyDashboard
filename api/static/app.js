/**
 * Shopify Multi-Agent Dropshipping Dashboard - Interactive Frontend Application
 */

document.addEventListener('DOMContentLoaded', () => {
  async function dashboardFetch(url, options = {}) {
    let token = sessionStorage.getItem('dashboardApiToken');
    if (!token) {
      token = window.prompt('Token de acceso privado al panel:') || '';
      if (!token) throw new Error('Se canceló el acceso al panel.');
      sessionStorage.setItem('dashboardApiToken', token);
    }
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 || response.status === 503) {
      const detail = await response.json().catch(() => ({}));
      if (response.status === 401) sessionStorage.removeItem('dashboardApiToken');
      window.alert(detail.detail || 'No se pudo autenticar el acceso al panel.');
      throw new Error(detail.detail || 'Acceso al panel rechazado.');
    }
    return response;
  }

  // Navigation tabs
  const navItems = document.querySelectorAll('.nav-item');
  const tabContents = document.querySelectorAll('.tab-content');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');

  const tabTitles = {
    overview: {
      title: 'Visión General del Sistema',
      subtitle: 'Monitoreo en tiempo real del ciclo de vida multi-agente de dropshipping.',
    },
    pipeline: {
      title: 'Ejecutor de Pipeline Multi-Agente',
      subtitle: 'Transforma catálogos de proveedores en productos comerciales con IA y temas Shopify.',
    },
    orders: {
      title: 'Auto-Fulfillment y Despacho',
      subtitle: 'Monitoreo de webhooks de compras, ruteo a proveedores y sincronización de tracking.',
    },
    catalog: {
      title: 'Catálogo de Productos',
      subtitle: 'Productos publicados en Shopify con descripciones de alta conversión y assets CDN.',
    },
    logs: {
      title: 'Registro de Incidentes y Logs',
      subtitle: 'Historial detallado de excepciones y actividad del sistema (error_log.txt).',
    },
  };

  navItems.forEach((item) => {
    item.addEventListener('click', () => {
      const tabKey = item.getAttribute('data-tab');
      switchTab(tabKey);
    });
  });

  function switchTab(tabKey) {
    navItems.forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tabKey);
    });
    tabContents.forEach((tab) => {
      tab.classList.toggle('active', tab.id === `tab-${tabKey}`);
    });
    if (tabTitles[tabKey]) {
      pageTitle.textContent = tabTitles[tabKey].title;
      pageSubtitle.textContent = tabTitles[tabKey].subtitle;
    }
    // Load fresh tab data
    if (tabKey === 'overview' || tabKey === 'orders') fetchOrders();
    if (tabKey === 'catalog') fetchProducts();
    if (tabKey === 'logs') fetchLogs();
  }

  // Quick navigation link from overview to orders
  const btnViewAllOrders = document.getElementById('btn-view-all-orders');
  if (btnViewAllOrders) {
    btnViewAllOrders.addEventListener('click', () => switchTab('orders'));
  }

  // Fetch Dashboard Stats
  async function fetchStats() {
    try {
      const res = await dashboardFetch('/api/stats');
      if (!res.ok) return;
      const data = await res.json();

      document.getElementById('kpi-products-count').textContent = data.products_count || 0;
      document.getElementById('kpi-orders-count').textContent = data.orders_count || 0;
      document.getElementById('kpi-tracking-count').textContent = data.tracking_synced_count || 0;
      document.getElementById('sidebar-shop-domain').textContent = data.shop_domain || 'da6gne-6x.myshopify.com';

      const modeBadge = document.getElementById('mode-text');
      if (data.dry_run) {
        modeBadge.textContent = 'MODO SANDBOX / SIMULACIÓN';
        modeBadge.parentElement.style.background = 'rgba(245, 158, 11, 0.1)';
        modeBadge.parentElement.style.borderColor = 'rgba(245, 158, 11, 0.3)';
        modeBadge.parentElement.style.color = 'var(--accent-amber)';
      } else {
        modeBadge.textContent = 'MODO LIVE CONECTADO';
        modeBadge.parentElement.style.background = 'rgba(16, 185, 129, 0.1)';
        modeBadge.parentElement.style.borderColor = 'rgba(16, 185, 129, 0.3)';
        modeBadge.parentElement.style.color = 'var(--accent-emerald)';
      }
    } catch (err) {
      console.warn('Error fetching stats:', err);
    }
  }

  // Fetch Orders History
  async function fetchOrders() {
    try {
      const res = await dashboardFetch('/api/orders');
      if (!res.ok) return;
      const orders = await res.json();

      const overviewTbody = document.getElementById('overview-orders-tbody');
      const ordersTbody = document.getElementById('orders-tbody');

      if (!orders || orders.length === 0) {
        const emptyRow = `<tr><td colspan="10" class="empty-state">No hay órdenes registradas aún. Haz clic en 'Simular Compra' para probar.</td></tr>`;
        overviewTbody.innerHTML = `<tr><td colspan="5" class="empty-state">No hay órdenes registradas aún.</td></tr>`;
        ordersTbody.innerHTML = emptyRow;
        return;
      }

      // Populate Overview recent table (up to 4)
      overviewTbody.innerHTML = orders
        .slice(0, 4)
        .map(
          (o) => `
          <tr>
            <td><strong>${o.shopify_order_number || '#1001'}</strong></td>
            <td>${o.customer_name || 'Cliente'}</td>
            <td><code style="color: var(--accent-amber);">${o.supplier_po_number || 'N/A'}</code></td>
            <td><span class="badge badge-accent">${o.tracking_code || 'En proceso'}</span></td>
            <td><span class="badge ${o.status.startsWith('FAILED') ? 'badge-danger' : 'badge-success'}">${o.status}</span></td>
          </tr>
        `
        )
        .join('');

      // Populate Full Orders Table
      ordersTbody.innerHTML = orders
        .map(
          (o) => `
          <tr>
            <td><strong>${o.shopify_order_number || '#1001'}</strong><br><small style="color: var(--text-muted);">${o.shopify_order_id}</small></td>
            <td>${o.fulfilled_at ? new Date(o.fulfilled_at).toLocaleTimeString() : 'Hoy'}</td>
            <td>${o.customer_name || 'Cliente'}</td>
            <td><small>${o.shipping_destination || 'US'}</small></td>
            <td>${(o.items_fulfilled || []).length} items</td>
            <td><code style="color: var(--accent-amber);">${o.supplier_po_number || 'N/A'}</code></td>
            <td><strong>${o.tracking_code || 'Pendiente'}</strong></td>
            <td>${o.tracking_company || 'CJ_DROPSHIPPING'}</td>
            <td>${o.shopify_fulfillment_synced ? '<span class="badge badge-success">✔ Sincronizado</span>' : '<span class="badge badge-outline">❌ No</span>'}</td>
            <td><span class="badge ${o.status.startsWith('FAILED') ? 'badge-danger' : 'badge-success'}">${o.status}</span></td>
          </tr>
        `
        )
        .join('');
    } catch (err) {
      console.warn('Error fetching orders:', err);
    }
  }

  // Fetch Catalog Products
  async function fetchProducts() {
    try {
      const res = await dashboardFetch('/api/products');
      if (!res.ok) return;
      const products = await res.json();
      const grid = document.getElementById('catalog-products-grid');

      if (!products || products.length === 0) {
        grid.innerHTML = `<div class="empty-state-box" style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">No hay productos registrados en el catálogo todavía. Ejecuta una ingesta en el tab 'Ejecutor Pipeline'.</div>`;
        return;
      }

      grid.innerHTML = products
        .map((p) => {
          const imgUrl = p.primary_cdn_url || 'https://placehold.co/400x300/111827/06B6D4?text=Shopify+Product+Asset';
          const cleanId = (p.shopify_product_id || '').split('/').pop();
          const adminUrl = `https://admin.shopify.com/store/da6gne-6x/products/${cleanId}`;

          return `
          <div class="product-card">
            <div class="product-image-container">
              <img src="${imgUrl}" alt="${p.optimized_seo_title}" class="product-image" onerror="this.src='https://placehold.co/400x300/111827/06B6D4?text=Shopify+Asset';" />
            </div>
            <div class="product-body">
              <span class="badge badge-accent" style="margin-bottom: 8px; align-self: flex-start;">${p.product_type || 'Fitness Equipment'}</span>
              <h4 class="product-title">${p.optimized_seo_title}</h4>
              <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 12px; line-height: 1.4;">${(p.hook || '').substring(0, 110)}...</p>
              <div class="product-meta">
                <div>
                  <span class="product-price">$${p.suggested_price || '0.00'}</span>
                  ${p.compare_at_price ? `<span class="product-compare-price">$${p.compare_at_price}</span>` : ''}
                </div>
                ${cleanId ? `<a href="${adminUrl}" target="_blank" class="btn btn-sm btn-outline">Ver en Admin ↗</a>` : ''}
              </div>
            </div>
          </div>
        `;
        })
        .join('');
    } catch (err) {
      console.warn('Error fetching products:', err);
    }
  }

  // Fetch Error Logs
  async function fetchLogs() {
    try {
      const res = await dashboardFetch('/api/logs');
      if (!res.ok) return;
      const data = await res.json();
      const logsElem = document.getElementById('error-logs-content');
      logsElem.textContent = data.logs || 'No se han registrado incidentes ni errores. El sistema opera normalmente.';
    } catch (err) {
      console.warn('Error fetching logs:', err);
    }
  }

  // Simulate Webhook Purchase
  async function simulateOrder() {
    const btn1 = document.getElementById('btn-quick-simulate-order');
    const btn2 = document.getElementById('btn-simulate-order-tab');
    [btn1, btn2].forEach((b) => {
      if (b) {
        b.disabled = true;
        b.textContent = 'Simulando...';
      }
    });

    try {
      const res = await dashboardFetch('/api/webhook/simulate', { method: 'POST' });
      const result = await res.json();
      alert(`✔ Orden ${result.fulfillment?.shopify_order_number || 'Generada'} procesada con éxito.\nPO Proveedor: ${result.fulfillment?.supplier_po_number}\nTracking: ${result.fulfillment?.tracking_code}`);
      fetchStats();
      fetchOrders();
    } catch (err) {
      alert(`Error al simular orden: ${err.message}`);
    } finally {
      if (btn1) {
        btn1.disabled = false;
        btn1.textContent = 'Simular Compra';
      }
      if (btn2) {
        btn2.disabled = false;
        btn2.textContent = '⚡ Simular Orden de Compra';
      }
    }
  }

  // Inventory & Price Sync Trigger
  async function syncInventory() {
    const btn = document.getElementById('btn-quick-sync-inventory');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Sincronizando...';
    }

    try {
      const res = await dashboardFetch('/api/inventory/sync', { method: 'POST' });
      const result = await res.json();
      const report = result.report;
      alert(`✔ Sincronización de Stock y Precios Completada.\n- Productos analizados: ${report.items_scanned}\n- Precios recalculados: ${report.price_adjustments}\n- Agotados detectados: ${report.stockouts_detected}\n- Duración: ${report.duration_sec}s`);
      fetchStats();
      fetchProducts();
    } catch (err) {
      alert(`Error al sincronizar inventario: ${err.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Sincronizar Stock';
      }
    }
  }

  document.getElementById('btn-quick-simulate-order')?.addEventListener('click', simulateOrder);
  document.getElementById('btn-simulate-order-tab')?.addEventListener('click', simulateOrder);
  document.getElementById('btn-quick-sync-inventory')?.addEventListener('click', syncInventory);

  // Pipeline Execution Trigger
  const btnStartPipeline = document.getElementById('btn-start-pipeline');
  const btnQuickRun = document.getElementById('btn-quick-run-pipeline');

  async function startPipelineRun(inputFile = null) {
    const file = inputFile || document.getElementById('pipeline-input-file').value;
    const mode = document.getElementById('pipeline-mode').value;
    const isLive = mode === 'live';

    switchTab('pipeline');

    const progressBox = document.getElementById('pipeline-progress-box');
    const progressBar = document.getElementById('pipeline-progress-bar');
    const statusText = document.getElementById('pipeline-status-text');
    const statusPercent = document.getElementById('pipeline-status-percent');
    const terminalLog = document.getElementById('pipeline-terminal-output');
    const resultsContainer = document.getElementById('pipeline-results-container');
    const resultsGrid = document.getElementById('pipeline-results-grid');

    progressBox.style.display = 'block';
    resultsContainer.style.display = 'none';
    progressBar.style.width = '15%';
    statusPercent.textContent = '15%';
    statusText.textContent = `Iniciando ingesta de [${file}] en modo ${isLive ? 'LIVE' : 'DRY-RUN'}...`;
    terminalLog.textContent = `[${new Date().toLocaleTimeString()}] Iniciando DropshippingPipelineOrchestrator...\n`;

    btnStartPipeline.disabled = true;

    try {
      terminalLog.textContent += `[${new Date().toLocaleTimeString()}] [CatalogAgent] Ingestando datos y aplicando SEO / Copywriting...\n`;
      progressBar.style.width = '45%';
      statusPercent.textContent = '45%';

      const res = await dashboardFetch('/api/pipeline/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_file: file, live: isLive }),
      });

      progressBar.style.width = '85%';
      statusPercent.textContent = '85%';
      terminalLog.textContent += `[${new Date().toLocaleTimeString()}] [ArtAgent & LiquidCoderAgent] Generando assets e inyectando templates...\n`;

      const data = await res.json();

      progressBar.style.width = '100%';
      statusPercent.textContent = '100%';
      statusText.textContent = `✔ Ingesta finalizada con éxito (${data.reports?.length || 0} productos procesados)`;
      terminalLog.textContent += `[${new Date().toLocaleTimeString()}] ✔ Pipeline completado exitosamente en ${data.total_duration_sec || 0}s\n`;

      if (data.reports && data.reports.length > 0) {
        resultsContainer.style.display = 'block';
        resultsGrid.innerHTML = data.reports
          .map(
            (r) => `
            <div class="result-card">
              <h4>${r.catalog_result?.optimized_seo_title}</h4>
              <p style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 8px;">Original: ${r.catalog_result?.original_title}</p>
              <div style="font-size: 0.88rem; margin-bottom: 6px;">
                <strong>Precio:</strong> $${r.catalog_result?.suggested_price} (Compare: $${r.catalog_result?.compare_at_price})
              </div>
              <div style="font-size: 0.88rem; margin-bottom: 8px;">
                <strong>Shopify ID:</strong> <code style="color: var(--accent-cyan);">${r.catalog_result?.shopify_product_id}</code>
              </div>
              ${
                r.catalog_result?.shopify_product_id
                  ? `<a href="https://admin.shopify.com/store/da6gne-6x/products/${r.catalog_result.shopify_product_id.split('/').pop()}" target="_blank" class="btn btn-sm btn-primary">Abrir en Shopify Admin ↗</a>`
                  : ''
              }
            </div>
          `
          )
          .join('');
      }

      fetchStats();
      fetchProducts();
    } catch (err) {
      statusText.textContent = `❌ Error en la ejecución: ${err.message}`;
      terminalLog.textContent += `[ERROR] ${err.message}\n`;
    } finally {
      btnStartPipeline.disabled = false;
    }
  }

  btnStartPipeline?.addEventListener('click', () => startPipelineRun());
  btnQuickRun?.addEventListener('click', () => startPipelineRun('data/catalogo_proveedor.csv'));

  // Quick Refresh buttons
  document.getElementById('btn-quick-sync')?.addEventListener('click', () => {
    fetchStats();
    fetchOrders();
    fetchProducts();
    fetchLogs();
  });
  document.getElementById('btn-refresh-catalog')?.addEventListener('click', fetchProducts);
  document.getElementById('btn-refresh-logs')?.addEventListener('click', fetchLogs);

  // Initial load
  fetchStats();
  fetchOrders();

  // Auto-polling every 10 seconds for real-time monitoring
  setInterval(() => {
    fetchStats();
  }, 10000);
});
