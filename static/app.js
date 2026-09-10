/*!
 * ODBM - Oracle Database Monitor
 * Copyright (C) 2025 Bruchsaal
 * SPDX-License-Identifier: AGPL-3.0-or-later
 * Source: https://github.com/Bruchsaal/odbm
 */
// Values rendered by autoTable come straight from the monitored database and
// are injected via x-html, so every one of them must be escaped first.
const ESCAPE_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);
}

document.addEventListener("alpine:init", () => {
  // -------------------------------------------------------------------------
  // 1. AUTO TABLE COMPONENT (Modified for SQL_ID Linking)
  // -------------------------------------------------------------------------
  Alpine.data("autoTable", () => ({
    html: '<div class="h-full flex items-center justify-center text-gray-300 text-xs italic">Waiting...</div>',
    rawData: [],
    sortCol: null,
    sortAsc: true,
    init() {
      // 1. Sort Handler
      this.$el.addEventListener("click", (e) => {
        const th = e.target.closest("th[data-sort]");
        if (th) this.sortBy(th.dataset.sort);
      });

      // 2. NEW: SQL_ID Click Handler
      this.$el.addEventListener("click", (e) => {
        // Check if user clicked a "jump" button we generated
        const btn = e.target.closest(".sql-jump-btn");
        if (btn) {
            e.stopPropagation();
            const sqlId = btn.dataset.sqlid;
            // Dispatch global event for dashboard to catch
            window.dispatchEvent(new CustomEvent('jump-to-sql', { detail: { sqlId } }));
        }
      });
    },
    initTable(data) {
      if (!data || !Array.isArray(data) || data.length === 0) {
        this.html = '<div class="h-full flex items-center justify-center text-gray-400 italic text-xs">No Data Available</div>';
        this.rawData = [];
        return;
      }
      if (data[0].error) {
        this.html = `<div class="h-full flex items-center justify-center p-4 text-center"><span class="text-xs font-mono text-red-600 bg-red-50 p-2 rounded border border-red-200 break-all">${esc(data[0].error)}</span></div>`;
        this.rawData = [];
        return;
      }
      if (data._resetSort === true) {
        this.sortCol = null;
        this.sortAsc = true;
      }
      this.rawData = data;
      this.render();
    },
    sortBy(col) {
      if (this.sortCol === col) this.sortAsc = !this.sortAsc;
      else {
        this.sortCol = col;
        this.sortAsc = true;
      }
      this.render();
    },
    render() {
      if (!this.rawData || this.rawData.length === 0) return;
      let rows = [...this.rawData];
      
      // Sort Logic
      if (this.sortCol) {
        rows.sort((a, b) => {
          let valA = a[this.sortCol];
          let valB = b[this.sortCol];
          if (valA == null) return 1;
          if (valB == null) return -1;
          const numA = parseFloat(valA);
          const numB = parseFloat(valB);
          if (!isNaN(numA) && !isNaN(numB) && String(valA).trim() !== "" && String(valB).trim() !== "") {
            return this.sortAsc ? numA - numB : numB - numA;
          }
          valA = String(valA).toLowerCase();
          valB = String(valB).toLowerCase();
          if (valA < valB) return this.sortAsc ? -1 : 1;
          if (valA > valB) return this.sortAsc ? 1 : -1;
          return 0;
        });
      }

      const cols = Object.keys(rows[0]).filter(k => k !== '_fetchedAt' && k !== '_resetSort');
      
      let s = '<table class="w-full text-left border-collapse"><thead class="bg-gray-50 sticky top-0 shadow-sm z-10"><tr>';
      cols.forEach((c) => {
        let arrow = "";
        let bgClass = "text-gray-600";
        if (this.sortCol === c) {
          arrow = this.sortAsc ? " ▲" : " ▼";
          bgClass = "text-blue-600 font-bold bg-blue-50";
        }
        s += `<th data-sort="${esc(c)}" class="border-b border-gray-300 px-2 py-1.5 text-[10px] uppercase select-none cursor-pointer hover:bg-gray-100 transition-colors ${bgClass} whitespace-nowrap">${esc(c)}${arrow}</th>`;
      });
      s += '</tr></thead><tbody class="bg-white divide-y divide-gray-100">';
      
      rows.forEach((row, i) => {
        s += `<tr class="${i % 2 === 0 ? "bg-white" : "bg-gray-50/50"} hover:bg-blue-50 transition-colors">`;
        cols.forEach((c) => {
          let val = row[c] !== null ? row[c] : "-";
          
          // --- NEW: Detect SQL_ID and make it interactive ---
          if (c === 'SQL_ID' && val !== '-') {
              s += `<td class="px-2 py-1 text-gray-700 whitespace-nowrap border-r border-transparent last:border-r-0">
                      <button class="sql-jump-btn text-blue-600 hover:text-blue-800 hover:underline font-mono font-bold flex items-center gap-1" data-sqlid="${esc(val)}" title="Analyze SQL">
                        ${esc(val)} <span class="text-[9px] opacity-50">🔍</span>
                      </button>
                    </td>`;
          } else {
              s += `<td class="px-2 py-1 text-gray-700 whitespace-nowrap border-r border-transparent last:border-r-0">${esc(val)}</td>`;
          }
        });
        s += "</tr>";
      });
      s += "</tbody></table>";
      this.html = s;
    },
  }));

  // --- Graph Controller (Unchanged) ---
  Alpine.data("graphController", () => ({
    sources: [], metrics: [], selectedSource: "", selectedMetric: "", chart: null, inspectorOpen: false, rawJson: "{}", selectedDate: "", viewStart: 0, viewEnd: 0,
    init() {
      const now = new Date();
      this.selectedDate = now.toISOString().split('T')[0];
      this.resetViewport();
      this.$watch("selectedSource", () => this.onSourceChange());
      this.$watch("selectedMetric", () => this.refreshChart(false, true));
      this.$watch("selectedDate", () => this.refreshChart(false, true));
      this.loadOptions();
      window.addEventListener("history-updated", () => {
        if (this.$el.offsetParent !== null) { this.refreshChart(true, false); }
        if (this.sources.length === 0) this.loadOptions();
      });
    },
    resetViewport() {
      this.viewStart = new Date(`${this.selectedDate}T00:00:00`).getTime();
      this.viewEnd = new Date(`${this.selectedDate}T23:59:59`).getTime();
    },
    async loadOptions() {
      try {
        const res = await fetch(`/api/history/options?_=${Date.now()}`).then((r) => r.json());
        this.sources = Object.keys(res);
        if (this.selectedSource && res[this.selectedSource]) { this.metrics = res[this.selectedSource]; }
      } catch (e) { console.error(e); }
    },
    onSourceChange() {
      this.selectedMetric = "";
      this.loadOptions().then(() => {
        fetch(`/api/history/options?_=${Date.now()}`).then((r) => r.json()).then((res) => {
            this.metrics = res[this.selectedSource] || [];
            this.refreshChart(false, true);
          });
      });
    },
    changeDate(delta) {
      const d = new Date(this.selectedDate);
      d.setDate(d.getDate() + delta);
      this.selectedDate = d.toISOString().split('T')[0];
    },
    parseTime(timeStr) {
      if (timeStr.includes("-")) return new Date(timeStr.replace(" ", "T")).getTime();
      return new Date(`${this.selectedDate}T${timeStr}`).getTime();
    },
    async fetchInspectorData() {
      if (!this.selectedSource) return;
      this.inspectorOpen = true;
      this.rawJson = "Loading...";
      try {
        const url = `/api/history/data?source=${encodeURIComponent(this.selectedSource)}&_=${Date.now()}`;
        const data = await fetch(url).then((r) => r.json());
        this.rawJson = JSON.stringify(data, null, 2);
      } catch (e) { this.rawJson = "Error: " + e; }
    },
    async refreshChart(quiet = false, forceReset = false) {
      const container = document.getElementById("historyChart");
      if (!this.selectedSource || !container) return;
      if (forceReset) this.resetViewport();
      const url = `/api/history/data?source=${encodeURIComponent(this.selectedSource)}&metric=${encodeURIComponent(this.selectedMetric)}&_=${Date.now()}`;
      const data = await fetch(url).then((r) => r.json());
      const dayStart = new Date(`${this.selectedDate}T00:00:00`).getTime();
      const dayEnd = new Date(`${this.selectedDate}T23:59:59`).getTime();
      const series = [];
      Object.keys(data).forEach((key) => {
        const points = data[key];
        if (points && points.length > 0) {
          const filtered = points.map(p => ({ x: this.parseTime(p.t), y: p.y })).filter(p => p.x >= dayStart && p.x <= dayEnd);
          if (filtered.length > 0) series.push({ name: key, data: filtered });
        }
      });
      if (!this.chart) {
        const options = {
          series: series,
          chart: { type: "area", height: "100%", animations: { enabled: false }, toolbar: { show: true, tools: { zoom: true, pan: true, reset: true } }, fontFamily: "sans-serif",
            events: { zoomed: (ctx, { xaxis }) => { this.viewStart = xaxis.min; this.viewEnd = xaxis.max; }, scrolled: (ctx, { xaxis }) => { this.viewStart = xaxis.min; this.viewEnd = xaxis.max; }, beforeResetZoom: () => { this.resetViewport(); } },
          },
          stroke: { width: 2, curve: "smooth" },
          xaxis: { type: "datetime", min: this.viewStart, max: this.viewEnd, labels: { datetimeFormatter: { hour: "HH:mm", minute: "HH:mm" } }, tooltip: { enabled: false } },
          yaxis: { labels: { style: { fontSize: "10px" } } },
          dataLabels: { enabled: false },
          fill: { type: "gradient", gradient: { opacityFrom: 0.4, opacityTo: 0.05 } },
          colors: ["#3b82f6", "#10b981", "#ef4444", "#f59e0b", "#8b5cf6"],
          grid: { borderColor: "#f1f5f9" },
          tooltip: { x: { format: "HH:mm:ss" }, theme: "light" },
        };
        this.chart = new ApexCharts(container, options);
        this.chart.render();
      } else {
        this.chart.updateSeries(series);
        this.chart.updateOptions({ xaxis: { min: this.viewStart, max: this.viewEnd } }, false, false);
      }
    },
  }));

  // -------------------------------------------------------------------------
  // 3. MAIN DASHBOARD LOGIC (Modified to listen for jumps)
  // -------------------------------------------------------------------------
  Alpine.data("dashboard", () => ({
    // State
    currentTab: "dashboard",
    detailTab: "parameter",
    config: { connections: [], active_index: -1 },
    widgets: {},
    widgetManager: { open: false, targetKey: null, search: "" },
    selectedConnIndex: -1,
    collectorActive: false,
    collectorInterval: 5,
    timer: null,
    lastUpdateStr: "-",
    dbInfo: { is_cdb: false, scope: "", warning: null, grant_hint: null },
    collector: { enabled: false, interval: 60, last_run: null, connections: [], cycles: 0, samples: 0 },
    alerts: { worst: "OK", count: 0, alerts: [] },
    alertsOpen: false,
    statusMessage: "Initializing...",
    errorMessage: "",
    
    // Data Stores
    data: { waitClasses: [], ownerSpace: [], latency: [], usedSpace: [], aas: [], fra: [], avail01: [], avail02: [], perf01: [], perf02: [], space01: [], asm01: [] },
    details: { 
        parameter: { sga: [], pga: [], opti: [], cursor: [] }, 
        tablespaces: { usage: [], datafiles: [] }, 
        sqlStats: { summary: [], single: [] }, 
        sqlDetails: { plan: [], text: [], sqlIdInput: "" }, 
        rman: { backups: [], errors: [], list: [] }, 
        usersProfiles: { users: [], profiles: [] }, 
        advanced: { advisor: [], alerts: [] }, 
        dbDump: { jobs: [], sessions: [], resumable: [] }, 
        longops: { data: [] } 
    },
    sessionsData: [], sessionsUpdateKey: 0, customResults: [], customSql: "", sqlMode: "QUERY", commandResult: null, selectedTemplate: "", sysInfoParsed: { ram: "-", sockets: "-", cores: "-", cpus: "-" }, logs: [], logFilter: "ALL", logSearch: "", 
    library: [], 
    libSearch: "", libEditing: false, libItem: { id: "", desc: "", sql: "", mode: "QUERY", refreshOptimum: 0 }, isNewEntry: false, sqlPaneHeight: 250, resizeState: { resizing: false, startY: 0, startHeight: 0 }, hasHistory: false, newConn: { name: "", user: "", password: "", dsn: "", sysdba: false }, editingIndex: null, testResult: null,
    dataCache: {},

    async init() {
      await this.fetchWidgetSettings(); 
      this.fetchConfig();
      this.fetchLogs();
      await this.fetchLibrary();   // getOptimum() needs this
      this.checkHistoryStatus();
      this.fetchDbInfo();
      this.fetchCollector();
      this.fetchAlerts();

      this.$watch('collectorInterval', () => this.handleTimerChange());
      this.handleTimerChange();

      // --- NEW: Global Listener for SQL Jumps ---
      window.addEventListener('jump-to-sql', (e) => {
          this.jumpToSqlDetails(e.detail.sqlId);
      });
    },

    // --- NEW: Handle SQL ID Jump ---
    async jumpToSqlDetails(sqlId) {
        console.log("Jumping to SQL:", sqlId);
        // 1. Set input value
        this.details.sqlDetails.sqlIdInput = sqlId;
        // 2. Switch tabs
        this.detailTab = 'sqldetails';
        // 3. Trigger load
        await this.fetchSqlDetails();
    },

    handleTimerChange() {
      if (this.timer) { clearInterval(this.timer); this.timer = null; }
      const ms = parseInt(this.collectorInterval) * 1000;
      if (ms <= 0) return;
      console.log(`[Timer] Auto-refresh active: ${ms}ms`);
      this.runCycle();
      this.timer = setInterval(() => this.runCycle(), ms);
    },

    toggleCollector() {
      this.setCollector(this.collectorActive, this.collector.interval || 60);
    },

    async checkDatabaseOnline() {
        const id = this.widgets['avail_status'] || '7';
        try {
            const res = await fetch(`/api/metric/${id}?history=false&_=${Date.now()}`);
            const json = await res.json();
            if (json.data && json.data.length > 0 && json.data[0].error) return false; 
            return true;
        } catch (e) { return false; }
    },

    async runCycle() {
        if (this.selectedConnIndex < 0) return;

        const isOnline = await this.checkDatabaseOnline();
        if (!isOnline) {
            this.statusMessage = "Unreachable";
            this.errorMessage = "Database Connection Failed"; 
            this.lastUpdateStr = "Retrying...";
            if (this.currentTab === 'dashboard') {
                await this.fetchDashboardData(false, "Connection Lost - Retrying...");
            }
            return;
        }

        const wasOffline = this.statusMessage !== "Online";
        this.statusMessage = "Online";
        if (wasOffline) this.fetchDbInfo();   // probe once the DB is reachable

        try {
            if (this.currentTab === 'dashboard') await this.fetchDashboardData();
            else if (this.currentTab === 'sessions') await this.fetchSessions();
            else if (this.currentTab === 'details') await this.refreshDetailView();
            else if (this.currentTab === 'logs') await this.fetchLogs();

            await this.fetchAlerts();
            if (this.collector.enabled) {
                this.checkHistoryStatus();
                window.dispatchEvent(new Event("history-updated"));
            }
            this.lastUpdateStr = new Date().toLocaleTimeString();
        } catch (e) {
            console.error("Cycle Error", e);
            this.errorMessage = "Auto-Refresh Error";
        }
    },

    // --- FETCH LOGIC ---
    async fetchDashboardData(silent = false, errorOverride = null) {
      if (this.selectedConnIndex < 0 || Object.keys(this.widgets).length === 0) return;

      const uiMap = {
          "wait_classes": "data.waitClasses", "owner_space": "data.ownerSpace", "latency": "data.latency", "used_space": "data.usedSpace", "aas": "data.aas", "fra": "data.fra", "avail_status": "data.avail01", "avail_invalid": "data.avail02", "perf_active": "data.perf01", "perf_blocking": "data.perf02", "space_visual": "data.space01", "asm_usage": "data.asm01", "sys_info": "FUNC_sys_info", "detailed_storage": "FUNC_detailed_storage"
      };

      const tasks = [];
      Object.keys(uiMap).forEach(key => {
          const id = this.widgets[key];
          if(!id) return;
          const p = (async () => {
              let res;
              if (errorOverride) res = [{ error: errorOverride, _fetchedAt: Date.now() }];
              else res = await this.fetchId(id);

              if (silent) return;

              if (this.currentTab === 'dashboard') {
                  const target = uiMap[key];
                  if (target === "FUNC_sys_info") this.parseSysInfo(res);
                  else if (target === "FUNC_detailed_storage") this.details.tablespaces.usage = res;
                  else if (target.startsWith("data.")) this.data[target.split('.')[1]] = res;
              }
          })();
          tasks.push(p);
      });

      await Promise.all(tasks);
    },

    async fetchId(idOrKey, force = false) {
      const id = this.widgets[idOrKey] || idOrKey;
      if (!force) {
          const optimum = this.getOptimum(id);
          const cached = this.dataCache[id];
          if (cached && optimum > 0) {
              const age = (Date.now() - cached.timestamp) / 1000;
              if (age < optimum) return cached.data;
          }
      }
      // The background collector owns history now, so viewing never writes.
      try {
        const res = await fetch(`/api/metric/${id}?history=false&_=${Date.now()}`).then((r) => r.json());
        const d = res.data || [];
        d._fetchedAt = Date.now();
        if (!d[0]?.error) this.dataCache[id] = { timestamp: Date.now(), data: d };
        return d;
      } catch (e) { return [{ error: "Fetch failed" }]; }
    },

    getOptimum(id) {
        const item = this.library.find(x => x.id === id);
        return (item && item.refreshOptimum) ? parseInt(item.refreshOptimum) : 0;
    },

    // --- TAB SWITCHING ---
    async switchDetailTab(subTab) {
      this.detailTab = subTab;
      await this.refreshDetailView();
    },

    async refreshDetailView() {
        const tabMap = {
            'parameter': ['sga', 'pga', 'opti', 'cursor'],
            'tablespaces': ['detailed_storage', 'datafiles'],
            'sqlstats': ['sql_summary', 'sql_single'],
            'sqldetails': [], 
            'rman': ['rman_backups', 'rman_errors', 'rman_list'],
            'usersprofiles': ['users', 'profiles'],
            'advanced': ['advisor', 'alerts'],
            'dbdump': ['dp_jobs', 'dp_sessions', 'resumable'],
            'longops': ['longops']
        };
        const widgetsToRefresh = tabMap[this.detailTab] || [];
        await Promise.all(widgetsToRefresh.map(key => this.refreshWidget(key)));
    },

    async refreshWidget(keyOrId) {
      const id = this.widgets[keyOrId] || keyOrId;
      try {
        const data = await this.fetchId(id, true); 
        const newData = Array.isArray(data) ? [...data] : [];
        if (data.length > 0 && data[0].error) newData[0] = data[0]; 
        newData._resetSort = true;

        if (keyOrId === "wait_classes") this.data.waitClasses = newData;
        else if (keyOrId === "owner_space") this.data.ownerSpace = newData;
        else if (keyOrId === "latency") this.data.latency = newData;
        else if (keyOrId === "used_space") this.data.usedSpace = newData;
        else if (keyOrId === "aas") this.data.aas = newData;
        else if (keyOrId === "fra") this.data.fra = newData;
        else if (keyOrId === "sga" && this.details.parameter) this.details.parameter.sga = newData;
        else if (keyOrId === "pga" && this.details.parameter) this.details.parameter.pga = newData;
        else if (keyOrId === "opti" && this.details.parameter) this.details.parameter.opti = newData;
        else if (keyOrId === "cursor" && this.details.parameter) this.details.parameter.cursor = newData;
        else if (keyOrId === "detailed_storage" && this.details.tablespaces) this.details.tablespaces.usage = newData;
        else if (keyOrId === "datafiles" && this.details.tablespaces) this.details.tablespaces.datafiles = newData;
        else if (keyOrId === "sql_summary" && this.details.sqlStats) this.details.sqlStats.summary = newData;
        else if (keyOrId === "sql_single" && this.details.sqlStats) this.details.sqlStats.single = newData;
        else if (keyOrId === "rman_backups" && this.details.rman) this.details.rman.backups = newData;
        else if (keyOrId === "rman_errors" && this.details.rman) this.details.rman.errors = newData;
        else if (keyOrId === "rman_list" && this.details.rman) this.details.rman.list = newData;
        else if (keyOrId === "users" && this.details.usersProfiles) this.details.usersProfiles.users = newData;
        else if (keyOrId === "profiles" && this.details.usersProfiles) this.details.usersProfiles.profiles = newData;
        else if (keyOrId === "advisor" && this.details.advanced) this.details.advanced.advisor = newData;
        else if (keyOrId === "alerts" && this.details.advanced) this.details.advanced.alerts = newData;
        else if (keyOrId === "dp_jobs" && this.details.dbDump) this.details.dbDump.jobs = newData;
        else if (keyOrId === "dp_sessions" && this.details.dbDump) this.details.dbDump.sessions = newData;
        else if (keyOrId === "resumable" && this.details.dbDump) this.details.dbDump.resumable = newData;
        else if (keyOrId === "longops" && this.details.longops) this.details.longops.data = newData;
        else if (keyOrId === "avail_status") this.data.avail01 = newData;
        else if (keyOrId === "avail_invalid") this.data.avail02 = newData;
        else if (keyOrId === "perf_active") this.data.perf01 = newData;
        else if (keyOrId === "perf_blocking") this.data.perf02 = newData;
        else if (keyOrId === "space_visual") this.data.space01 = newData;
        else if (keyOrId === "asm_usage") this.data.asm01 = newData;
        else if (keyOrId === "sessions_tab") { this.sessionsData = newData; this.sessionsUpdateKey++; }
        
        this.statusMessage = "Online";
      } catch (e) { 
          console.error("Refresh Error for " + keyOrId, e);
          this.errorMessage = e; 
      }
    },

    // --- Helpers (Standard) ---
    async fetchSessions() {
      try {
        const id = this.widgets["sessions_tab"] || "20";
        const res = await this.fetchId(id, true);
        this.sessionsData = [...res]; 
        this.sessionsUpdateKey++;     
      } catch (e) { this.errorMessage = e; }
    },
    async fetchWidgetSettings() {
      try {
        this.widgets = await fetch(`/api/widgets?_=${Date.now()}`).then((r) => r.json());
      } catch (e) { console.error(e); }
    },
    getWidgetTitle(key, defaultTitle) {
      const id = this.widgets[key];
      if (!id) return defaultTitle;
      const item = this.library.find((x) => x.id === id);
      return item ? item.desc : defaultTitle;
    },
    openWidgetManager(widgetKey) {
      this.widgetManager.targetKey = widgetKey;
      this.widgetManager.search = "";
      this.widgetManager.open = true;
      if (this.library.length === 0) this.fetchLibrary();
    },
    async selectWidgetQuery(queryId) {
      if (!this.widgetManager.targetKey) return;
      this.widgets[this.widgetManager.targetKey] = queryId;
      try {
        await fetch("/api/widgets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: this.widgets }) });
      } catch (e) {}
      await this.refreshWidget(this.widgetManager.targetKey);
      this.widgetManager.open = false;
    },
    async jumpToEditor(keyOrId) {
      const id = this.widgets[keyOrId] || keyOrId;
      this.currentTab = "library";
      this.libSearch = id;
      await this.fetchLibrary();
    },
    testEditorQuery() {
      this.customSql = this.libItem.sql;
      let m = this.libItem.mode || "QUERY";
      this.sqlMode = m;
      this.customResults = [];
      this.commandResult = null;
      this.currentTab = "custom";
      this.$nextTick(() => { this.runCustomSql(); });
    },
    testQuery(item) {
      this.customSql = item.sql;
      this.sqlMode = item.mode === "COMMAND" ? "COMMAND" : "QUERY";
      this.currentTab = "custom";
      this.runCustomSql();
    },
    openLibEditor(item = null) {
      if (item) {
        this.libItem = { ...item };
        if (!this.libItem.mode) this.libItem.mode = "QUERY";
        if (this.libItem.refreshOptimum == null) this.libItem.refreshOptimum = 0;
        this.isNewEntry = false;
      } else {
        const ids = this.library.map((q) => parseInt(q.id)).filter((n) => !isNaN(n));
        const maxId = ids.length > 0 ? Math.max(...ids) : 0;
        this.libItem = { id: (maxId + 1).toString(), desc: "", sql: "", mode: "QUERY", refreshOptimum: 0 };
        this.isNewEntry = true;
      }
      this.libEditing = true;
    },
    async saveConnection() {
      if (!this.newConn.name || !this.newConn.user || !this.newConn.dsn) return alert("Please fill all fields");
      // Capture before cancelEdit() clears editingIndex, otherwise the
      // "did I just edit the active connection?" check below never fires.
      const targetIndex = this.editingIndex;
      const connData = { ...this.newConn };
      if (targetIndex !== null) {
        this.config.connections[targetIndex] = connData;
      } else {
        this.config.connections.push(connData);
      }
      await this.persistConfig();
      this.cancelEdit();
      // Re-read so server-assigned ids and has_password flags land in state.
      await this.fetchConfig();
      this.fetchCollector();
      if (targetIndex === this.selectedConnIndex) {
        this.dataCache = {};
        await this.switchConnection();
      }
    },
    async saveLibItem() {
      if (!this.libItem.id || !this.libItem.sql) return alert("ID and SQL are required");
      const payload = { ...this.libItem };
      const n = parseInt(payload.refreshOptimum);
      payload.refreshOptimum = isNaN(n) || n < 0 ? 0 : n;
      const res = await fetch("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) return alert("Save failed: " + res.status);
      this.libEditing = false;
      await this.fetchLibrary();
    },
    async deleteLibItem(id) {
      if (!confirm("Delete?")) return;
      await fetch("/api/library/" + id, { method: "DELETE" });
      this.fetchLibrary();
    },
    async resetLibrary() {
      if (!confirm("Reset?")) return;
      await fetch("/api/library/reset", { method: "POST" });
      await this.fetchLibrary();
    },
    async runCustomSql() {
      if (!this.customSql) return;
      try {
        const endpoint = this.sqlMode === "QUERY" ? "/api/execute" : "/api/execute/command";
        const res = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql: this.customSql }) }).then((r) => r.json());
        if (this.sqlMode === "QUERY") {
          this.customResults = res.data;
        } else {
          this.commandResult = res.data && res.data.length > 0 ? res.data[0] : { error: "Unknown response" };
        }
      } catch (e) {
        if (this.sqlMode === "COMMAND") this.commandResult = { error: e };
      }
    },
    get filteredTemplates() {
      if (!this.library) return [];
      const m = this.sqlMode;
      return this.library.filter((item) => {
        let tm = item.mode || "QUERY";
        if (item.desc && item.desc.includes("Command")) tm = "COMMAND";
        return tm === m;
      });
    },
    loadTemplate() {
      const item = this.library.find((x) => x.id === this.selectedTemplate);
      if (item) this.customSql = item.sql;
    },
    async fetchSqlDetails() {
      const id = this.details.sqlDetails.sqlIdInput.trim();
      if (!id) return;
      if (this.library.length === 0) await this.fetchLibrary();
      const q14 = this.library.find((x) => x.id === "14");
      const q15 = this.library.find((x) => x.id === "15");
      if (!q14 || !q15) return;
      // A SQL_ID is always alphanumeric; refuse anything else outright.
      if (!/^[A-Za-z0-9_]{1,32}$/.test(id)) {
        this.errorMessage = "Invalid SQL_ID";
        return;
      }
      try {
        // Prefer a bind variable. Libraries upgraded from an older version may
        // still carry the '' placeholder form, so fall back to substitution.
        const body = (tpl) =>
          tpl.includes(":sql_id")
            ? JSON.stringify({ sql: tpl, binds: { sql_id: id } })
            : JSON.stringify({ sql: tpl.replace("''", `'${id}'`) });
        const post = (tpl) =>
          fetch("/api/execute", { method: "POST", headers: { "Content-Type": "application/json" }, body: body(tpl) }).then((r) => r.json());
        const [resPlan, resText] = await Promise.all([post(q14.sql), post(q15.sql)]);
        this.details.sqlDetails.plan = resPlan.data;
        this.details.sqlDetails.text = resText.data;
      } catch (e) { console.error("SQL Details Error", e); }
    },
    parseSysInfo(rows) {
      if (!rows || rows.length === 0 || rows[0].error) {
        this.sysInfoParsed = { ram: "-", sockets: "-", cores: "-", cpus: "-" };
        return;
      }
      let info = { ram: "-", sockets: "-", cores: "-", cpus: "-" };
      rows.forEach((r) => {
        const name = r.STAT_NAME;
        const val = r.VALUE || r.RAM;
        if (name === "NUM_CPUS") info.cpus = val;
        if (name === "NUM_CPU_CORES") info.cores = val;
        if (name === "NUM_CPU_SOCKETS") info.sockets = val;
        if (name === "PHYSICAL_MEMORY_BYTES") info.ram = val;
      });
      this.sysInfoParsed = info;
    },
    get activeConnectionString() {
      if (this.selectedConnIndex >= 0 && this.config.connections[this.selectedConnIndex]) {
        const c = this.config.connections[this.selectedConnIndex];
        return `${c.user}@${c.dsn}`;
      }
      return "Not Connected";
    },
    get statusColorClass() {
      if (this.statusMessage.includes("Online")) return "bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.5)]";
      if (this.statusMessage.includes("Error") || this.statusMessage.includes("Unreachable")) return "bg-red-500";
      return "bg-slate-500";
    },
    navClass(tab) {
      return this.currentTab === tab ? "bg-blue-600 text-white shadow-md" : "text-slate-400 hover:bg-slate-800 hover:text-white";
    },
    editConnection(index) {
      this.editingIndex = index;
      // The server redacts passwords, so the box starts empty and an empty
      // box means "keep the stored secret".
      this.newConn = { ...this.config.connections[index], password: "" };
    },
    cancelEdit() {
      this.editingIndex = null;
      this.newConn = { id: "", name: "", user: "", password: "", dsn: "", sysdba: false, collect: false };
      this.testResult = null;
    },
    async removeConnection(index) {
      if (!confirm("Remove?")) return;
      this.config.connections.splice(index, 1);
      if (this.config.active_index === index) {
        this.config.active_index = -1;
        this.selectedConnIndex = -1;
      } else if (this.config.active_index > index) {
        this.config.active_index--;
        this.selectedConnIndex = this.config.active_index;
      }
      this.cancelEdit();
      this.switchConnection();
    },
    async fetchConfig() {
      try {
        const res = await fetch(`/api/config?_=${Date.now()}`).then((r) => r.json());
        this.config = res;
        this.selectedConnIndex = res.active_index;
        if (this.selectedConnIndex >= 0) this.fetchDashboardData();
      } catch (e) {}
    },
    async switchConnection() {
      this.config.active_index = parseInt(this.selectedConnIndex);
      if (this.selectedConnIndex == -1) return;
      await this.persistConfig();
      this.clearData();
      this.dataCache = {}; 
      this.fetchDbInfo();
      this.fetchDashboardData();
    },
    async persistConfig() {
      await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.config) });
    },
    clearData() {
      this.data = { waitClasses: [], ownerSpace: [], latency: [], usedSpace: [], aas: [], fra: [], avail01: [], avail02: [], perf01: [], perf02: [], space01: [], asm01: [] };
    },
    async fetchLogs() {
      try {
        const res = await fetch(`/api/logs?_=${Date.now()}`).then((r) => r.json());
        if (Array.isArray(res)) this.logs = res;
      } catch (e) {}
    },
    async clearLogs() {
      await fetch("/api/logs/clear", { method: "POST" });
      this.logs = [];
    },
    get filteredLogs() {
      let res = this.logs;
      if (this.logFilter !== "ALL") {
        res = res.filter((l) => l.level === this.logFilter);
      }
      if (this.logSearch) {
        const s = this.logSearch.toLowerCase();
        res = res.filter((l) => (l.message && l.message.toLowerCase().includes(s)) || (l.source && l.source.toLowerCase().includes(s)) || (l.details && l.details.toLowerCase().includes(s)));
      }
      return res;
    },
    get errorCount() {
      return this.logs.filter((l) => l.level === "ERROR").length;
    },
    async fetchLibrary() {
      const res = await fetch(`/api/library?_=${Date.now()}`).then((r) => r.json());
      this.library = res;
    },
    get filteredLibrary() {
      if (!this.libSearch) return this.library;
      const s = this.libSearch.toLowerCase();
      return this.library.filter((l) => l.id.includes(s) || l.desc.toLowerCase().includes(s));
    },
    resizeStart(e, mode, targetObj) {
      this.resizeState.resizing = true;
      this.resizeState.startX = e.clientX;
      this.resizeState.startY = e.clientY;
      this.resizeState.mode = mode;
      this.resizeState.target = targetObj;
      if (mode === "h" || mode === "local") {
        this.resizeState.startVal = targetObj.h || this.sqlPaneHeight;
        document.body.style.cursor = "row-resize";
      } else {
        this.resizeState.startVal = targetObj.w;
        this.resizeState.containerWidth = e.target.parentElement.offsetWidth;
        document.body.style.cursor = "col-resize";
      }
      document.body.style.userSelect = "none";
    },
    resizeMove(e) {
      if (!this.resizeState.resizing) return;
      if (this.resizeState.mode === "h") {
        const delta = e.clientY - this.resizeState.startY;
        this.resizeState.target.h = Math.max(100, this.resizeState.startVal + delta);
      } else if (this.resizeState.mode === "w") {
        const delta = e.clientX - this.resizeState.startX;
        const deltaPercent = (delta / this.resizeState.containerWidth) * 100;
        this.resizeState.target.w = Math.max(20, Math.min(80, this.resizeState.startVal + deltaPercent));
      } else if (this.resizeState.mode === "local") {
        const delta = e.clientY - this.resizeState.startY;
        this.sqlPaneHeight = Math.max(100, this.resizeState.startVal + delta);
      }
    },
    resizeStop() {
      this.resizeState.resizing = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    async testConnection() {
      this.testResult = { status: "loading", message: "Testing..." };
      if (!this.newConn.user || !this.newConn.dsn) {
        this.testResult = { status: "error", message: "Missing User or DSN" };
        return;
      }
      try {
        const res = await fetch("/api/config/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.newConn) }).then((r) => r.json());
        this.testResult = res;
      } catch (e) {
        this.testResult = { status: "error", message: "Server Error" };
      }
    },
    sessionCount(key) {
      // PERF_01 returns one pivoted row of named counters.
      const row = (this.data.perf01 && this.data.perf01[0]) || {};
      if (row.error) return 0;
      return row[key] ?? row[key.toUpperCase()] ?? 0;
    },
    async fetchCollector() {
      try {
        this.collector = await fetch(`/api/collector?_=${Date.now()}`).then((r) => r.json());
        this.collectorActive = this.collector.enabled;
      } catch (e) {}
    },
    async setCollector(enabled, interval) {
      try {
        this.collector = await fetch("/api/collector", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled, interval: parseInt(interval) || 60 }),
        }).then((r) => r.json());
        this.collectorActive = this.collector.enabled;
        this.checkHistoryStatus();
      } catch (e) {}
    },
    async fetchAlerts() {
      try {
        this.alerts = await fetch(`/api/alerts?_=${Date.now()}`).then((r) => r.json());
      } catch (e) {}
    },
    get alertBannerClass() {
      return this.alerts.worst === "CRIT"
        ? "border-red-300 bg-red-50 text-red-900"
        : "border-amber-300 bg-amber-50 text-amber-900";
    },
    async fetchDbInfo() {
      try {
        this.dbInfo = await fetch(`/api/dbinfo?_=${Date.now()}`).then((r) => r.json());
      } catch (e) {
        this.dbInfo = { is_cdb: false, scope: "", warning: null, grant_hint: null };
      }
    },
    async checkHistoryStatus() {
      try {
        const opts = await fetch(`/api/history/options?_=${Date.now()}`).then((r) => r.json());
        this.hasHistory = Object.keys(opts).length > 0;
      } catch (e) { this.hasHistory = false; }
    },
    switchTab(tab) {
      this.currentTab = tab;
      if (tab === "sessions") this.fetchSessions();
      if (tab === "library") this.fetchLibrary();
      if (tab === "logs") this.fetchLogs();
      if (tab === "details") this.switchDetailTab("parameter");
      if (tab === "graphs") {
        this.$nextTick(() => {
          const el = document.getElementById("graph-root");
          if (el && el._x_dataStack) el._x_dataStack[0].loadOptions();
        });
      }
    },
  }));
});