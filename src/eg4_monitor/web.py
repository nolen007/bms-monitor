"""
Web GUI for BMS Battery Monitor - Multi-battery support.
"""

import json
import logging
import threading
from datetime import datetime
from typing import List, Optional
from flask import Flask, render_template_string, jsonify

from .battery import BatteryData, format_duration
from .energy_ledger import EnergyLedger

logger = logging.getLogger(__name__)


# HTML Template - see _get_template() method
class WebServer:
    """Flask web server for battery monitoring GUI."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5000, low_soc_cutoff: float = 0.0, ha_circuits: list = None):
        self.host = host
        self.port = port
        self.low_soc_cutoff = low_soc_cutoff
        self.ha_circuits = ha_circuits or []
        self.app = Flask(__name__)
        self.batteries: List[BatteryData] = []
        self.mqtt_connected: bool = False
        self.pack_time_to_low_hours: float = None
        self.energy_ledger: Optional[EnergyLedger] = None
        self._thread = None

        # Register routes
        self.app.add_url_rule('/', 'index', self._index)
        self.app.add_url_rule('/api/data', 'api_data', self._api_data)
        self.app.add_url_rule('/api/savings', 'api_savings', self._api_savings)
        
        # Disable Flask logging in production
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.WARNING)
    
    def _index(self):
        """Serve the main dashboard page."""
        return render_template_string(self._get_template())
    
    def _api_data(self):
        """Return current battery data as JSON."""
        return jsonify({
            'batteries': [b.to_dict() for b in self.batteries],
            'mqtt_connected': self.mqtt_connected,
            'low_soc_cutoff': self.low_soc_cutoff,
            'pack_time_to_low_hours': self.pack_time_to_low_hours,
            'pack_time_to_low': format_duration(self.pack_time_to_low_hours),
        })

    def _api_savings(self):
        """Return today's/lifetime solar/battery savings, grid cost, and tracked circuits."""
        if not self.energy_ledger:
            return jsonify({'enabled': False})

        circuits = []
        for c in self.ha_circuits:
            entry = {
                'name': c.name,
                'is_main': c.is_main,
                'today': self.energy_ledger.circuit_totals_today(c.name).to_dict(),
                'this_month': self.energy_ledger.circuit_totals_this_month(c.name).to_dict(),
                'lifetime': self.energy_ledger.circuit_totals_lifetime(c.name).to_dict(),
                'current_power': (self.energy_ledger.circuit_latest(c.name) or {}).get('power'),
            }
            if c.is_main:
                entry['monthly_bill_estimate'] = self.energy_ledger.monthly_bill_estimate(c.name)
            circuits.append(entry)

        return jsonify({
            'enabled': True,
            'today': self.energy_ledger.totals_today().to_dict(),
            'lifetime': self.energy_ledger.totals_lifetime().to_dict(),
            'current': self.energy_ledger.latest(),
            'circuits': circuits,
        })

    def update_data(self, batteries: List[BatteryData], mqtt_connected: bool = False, pack_time_to_low_hours: float = None):
        """Update the current battery data."""
        self.batteries = batteries
        self.mqtt_connected = mqtt_connected
        self.pack_time_to_low_hours = pack_time_to_low_hours
    
    def start(self):
        """Start the web server in a background thread."""
        self._thread = threading.Thread(
            target=self._run_server,
            daemon=True
        )
        self._thread.start()
        logger.info(f"Web server started at http://{self.host}:{self.port}")
    
    def _run_server(self):
        """Run the Flask server."""
        self.app.run(
            host=self.host,
            port=self.port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    
    def stop(self):
        """Stop the web server."""
        logger.info("Web server stopping")
    
    def _get_template(self):
        """Return the HTML template."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="BMS Monitor">
    <meta name="theme-color" content="#1a1a2e">
    <link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔋</text></svg>">
    <title>BMS Battery Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #eee; min-height: 100vh; padding: 20px; padding-top: env(safe-area-inset-top, 20px); padding-bottom: env(safe-area-inset-bottom, 20px); }
        .container { max-width: 1600px; margin: 0 auto; }
        header { text-align: center; margin-bottom: 30px; }
        header h1 { font-size: 2.5em; color: #00d4ff; }
        .status-bar { display: flex; justify-content: center; gap: 30px; margin-top: 10px; color: #888; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #888; display: inline-block; margin-right: 5px; }
        .status-dot.online { background: #00ff88; box-shadow: 0 0 10px #00ff88; }
        .status-dot.offline { background: #ff4444; }
        .summary-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .summary-card { background: rgba(0, 212, 255, 0.1); border: 1px solid rgba(0, 212, 255, 0.3); border-radius: 12px; padding: 20px; text-align: center; }
        .summary-card .label { font-size: 0.85em; color: #888; }
        .summary-card .value { font-size: 2em; font-weight: 700; color: #00d4ff; }
        .summary-card .unit { font-size: 0.5em; color: #888; }
        .battery-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 20px; }
        .battery-card { background: rgba(255,255,255,0.05); border-radius: 16px; padding: 24px; border: 1px solid rgba(255,255,255,0.1); }
        .battery-card.offline { opacity: 0.6; border-color: rgba(255,68,68,0.3); }
        .battery-card.alarm { border-color: #ff4444; box-shadow: 0 0 20px rgba(255,68,68,0.2); }
        .battery-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .battery-name { font-size: 1.3em; font-weight: 600; color: #00d4ff; }
        .battery-status { padding: 5px 12px; border-radius: 20px; font-size: 0.8em; }
        .battery-status.online { background: rgba(0,255,136,0.2); color: #00ff88; }
        .battery-status.offline { background: rgba(255,68,68,0.2); color: #ff4444; }
        .battery-status.alarm { background: rgba(255,68,68,0.3); color: #ff4444; animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .metrics-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 15px; }
        .metric { background: rgba(0,0,0,0.2); padding: 12px; border-radius: 8px; text-align: center; }
        .metric .label { font-size: 0.7em; color: #888; }
        .metric .value { font-size: 1.4em; font-weight: 600; }
        .metric .unit { font-size: 0.5em; color: #888; }
        .soc-bar { height: 20px; background: rgba(255,255,255,0.1); border-radius: 10px; overflow: hidden; margin: 10px 0; }
        .soc-fill { height: 100%; border-radius: 10px; transition: width 0.5s; }
        .soc-fill.high { background: linear-gradient(90deg, #00ff88, #00d4ff); }
        .soc-fill.medium { background: linear-gradient(90deg, #ffaa00, #ff6b00); }
        .soc-fill.low { background: linear-gradient(90deg, #ff4444, #ff0000); }
        .cell-grid { display: grid; grid-template-columns: repeat(8, 1fr); gap: 5px; margin-top: 10px; }
        .cell { background: rgba(0,0,0,0.3); padding: 6px 3px; border-radius: 5px; text-align: center; font-size: 0.8em; border: 2px solid transparent; }
        .cell.min { border-color: #ff6b6b; }
        .cell.max { border-color: #00ff88; }
        .cell .cell-num { font-size: 0.65em; color: #666; }
        .cell .cell-voltage { font-family: monospace; }
        .alarm-list { background: rgba(255,68,68,0.1); border: 1px solid rgba(255,68,68,0.3); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
        .alarm-item { color: #ff6b6b; padding: 3px 0; font-size: 0.9em; }
        .alarm-item::before { content: "⚠️ "; }
        .last-update { text-align: center; color: #666; margin-top: 30px; font-size: 0.85em; }
        .savings-section { margin-bottom: 30px; }
        .savings-section h2 { font-size: 1.1em; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; text-align: center; }
        .savings-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; }
        .savings-col { background: rgba(255,255,255,0.05); border-radius: 12px; padding: 16px; border: 1px solid rgba(255,255,255,0.1); }
        .savings-col .period { text-align: center; font-size: 0.8em; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
        .savings-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
        .savings-metric { background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px; text-align: center; }
        .savings-metric .label { font-size: 0.7em; color: #888; }
        .savings-metric .value { font-size: 1.3em; font-weight: 600; }
        .savings-metric.saved .value { color: #00ff88; }
        .savings-metric.cost .value { color: #ffaa00; }
        .circuit-col .period { display: flex; justify-content: space-between; align-items: center; }
        .bill-estimate { text-align: center; background: rgba(0,212,255,0.1); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
        .bill-estimate .label { font-size: 0.7em; color: #888; }
        .bill-estimate .value { font-size: 1.8em; font-weight: 700; color: #00d4ff; }
        
        /* Tablet */
        @media (max-width: 1024px) {
            .battery-grid { grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
        }
        
        /* Mobile */
        @media (max-width: 768px) {
            body { padding: 10px; }
            header h1 { font-size: 1.6em; }
            .status-bar { gap: 15px; font-size: 0.9em; }
            .summary-row { grid-template-columns: repeat(2, 1fr); gap: 10px; }
            .summary-card { padding: 12px; }
            .summary-card .value { font-size: 1.5em; }
            .battery-grid { grid-template-columns: 1fr; gap: 15px; }
            .battery-card { padding: 16px; }
            .battery-name { font-size: 1.1em; }
            .metrics-row { grid-template-columns: repeat(2, 1fr); gap: 8px; }
            .metric { padding: 10px 6px; }
            .metric .value { font-size: 1.2em; }
            .cell-grid { grid-template-columns: repeat(4, 1fr); gap: 4px; }
            .cell { padding: 5px 2px; font-size: 0.75em; }
        }
        
        /* Small mobile */
        @media (max-width: 400px) {
            header h1 { font-size: 1.3em; }
            .summary-row { grid-template-columns: 1fr 1fr; }
            .summary-card .value { font-size: 1.3em; }
            .metrics-row { grid-template-columns: repeat(2, 1fr); }
            .cell-grid { grid-template-columns: repeat(4, 1fr); }
            .cell .cell-voltage { font-size: 0.9em; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔋 BMS Battery Monitor</h1>
            <div class="status-bar">
                <span><span class="status-dot" id="mqtt-status"></span> MQTT</span>
                <span id="battery-count">0 Batteries</span>
            </div>
        </header>
        <div class="summary-row">
            <div class="summary-card"><div class="label">Total Energy</div><div class="value"><span id="total-kwh">--</span><span class="unit"> kWh</span></div></div>
            <div class="summary-card"><div class="label">Total Power</div><div class="value"><span id="total-power">--</span><span class="unit"> W</span></div></div>
            <div class="summary-card"><div class="label">Average SOC</div><div class="value"><span id="avg-soc">--</span><span class="unit"> %</span></div></div>
            <div class="summary-card"><div class="label">Online</div><div class="value"><span id="online-count">--</span></div></div>
            <div class="summary-card"><div class="label" id="ttl-label">Time to Low SOC</div><div class="value" id="ttl-pack">--</div></div>
        </div>
        <div class="savings-section" id="savings-section" style="display:none">
            <h2>💰 Solar &amp; Battery Savings</h2>
            <div class="savings-col" style="margin-bottom:15px">
                <div class="period">Live Inputs <span id="inputs-asof" style="text-transform:none;letter-spacing:normal"></span></div>
                <div class="savings-metrics">
                    <div class="savings-metric"><div class="label">PV Power</div><div class="value"><span id="in-pv-power">--</span><span class="unit" style="font-size:0.6em"> W</span></div></div>
                    <div class="savings-metric"><div class="label">Grid Power</div><div class="value"><span id="in-grid-power">--</span><span class="unit" style="font-size:0.6em"> W</span></div></div>
                    <div class="savings-metric"><div class="label">Battery Power</div><div class="value"><span id="in-battery-power">--</span><span class="unit" style="font-size:0.6em"> W</span></div></div>
                    <div class="savings-metric"><div class="label">Rate</div><div class="value">$<span id="in-rate">--</span><span class="unit" style="font-size:0.6em">/kWh</span></div></div>
                    <div class="savings-metric"><div class="label">Grid Charging</div><div class="value" id="in-grid-charging">--</div></div>
                </div>
            </div>
            <div class="savings-columns">
                <div class="savings-col">
                    <div class="period">Today</div>
                    <div class="savings-metrics">
                        <div class="savings-metric saved"><div class="label">Solar Saved</div><div class="value">$<span id="today-solar-savings">--</span></div></div>
                        <div class="savings-metric saved"><div class="label">Battery Saved (net)</div><div class="value">$<span id="today-battery-savings">--</span></div></div>
                        <div class="savings-metric cost"><div class="label">Grid Cost</div><div class="value">$<span id="today-grid-cost">--</span></div></div>
                        <div class="savings-metric cost"><div class="label">Grid Charging Cost</div><div class="value">$<span id="today-grid-charge-cost">--</span></div></div>
                        <div class="savings-metric saved"><div class="label">Total Saved</div><div class="value">$<span id="today-total-savings">--</span></div></div>
                    </div>
                </div>
                <div class="savings-col">
                    <div class="period">Lifetime</div>
                    <div class="savings-metrics">
                        <div class="savings-metric saved"><div class="label">Solar Saved</div><div class="value">$<span id="life-solar-savings">--</span></div></div>
                        <div class="savings-metric saved"><div class="label">Battery Saved (net)</div><div class="value">$<span id="life-battery-savings">--</span></div></div>
                        <div class="savings-metric cost"><div class="label">Grid Cost</div><div class="value">$<span id="life-grid-cost">--</span></div></div>
                        <div class="savings-metric cost"><div class="label">Grid Charging Cost</div><div class="value">$<span id="life-grid-charge-cost">--</span></div></div>
                        <div class="savings-metric saved"><div class="label">Total Saved</div><div class="value">$<span id="life-total-savings">--</span></div></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="savings-section circuits-section" id="circuits-section" style="display:none">
            <h2>🔌 Tracked Circuits</h2>
            <div class="savings-columns" id="circuits-grid"></div>
        </div>
        <div class="battery-grid" id="battery-grid"></div>
        <div class="last-update">Last updated: <span id="timestamp">--</span></div>
    </div>
    <script>
        function createBatteryCard(b) {
            const online = b.online, alarms = b.alarms && b.alarms.length > 0;
            let st = online ? 'online' : 'offline', stxt = online ? 'Online' : 'Offline';
            if (alarms) { st = 'alarm'; stxt = 'ALARM'; }
            const socClass = b.soc > 50 ? 'high' : b.soc > 20 ? 'medium' : 'low';
            let cells = '';
            if (b.cell_voltages && b.cell_voltages.length) {
                const minV = Math.min(...b.cell_voltages), maxV = Math.max(...b.cell_voltages);
                cells = b.cell_voltages.map((v,i) => `<div class="cell ${v===minV?'min':''} ${v===maxV?'max':''}"><div class="cell-num">C${i+1}</div><div class="cell-voltage">${v.toFixed(3)}</div></div>`).join('');
            }
            let alarmHtml = alarms ? `<div class="alarm-list">${b.alarms.map(a=>`<div class="alarm-item">${a}</div>`).join('')}</div>` : '';
            return `<div class="battery-card ${!online?'offline':''} ${alarms?'alarm':''}">
                <div class="battery-header"><div class="battery-name">${b.name||b.battery_id}</div><div class="battery-status ${st}">${stxt}</div></div>
                ${alarmHtml}
                <div style="display:flex;justify-content:space-between;font-size:0.9em;"><span>SOC</span><span>${b.soc?.toFixed(1)||'--'}%</span></div>
                <div class="soc-bar"><div class="soc-fill ${socClass}" style="width:${b.soc||0}%"></div></div>
                <div class="metrics-row">
                    <div class="metric"><div class="label">Voltage</div><div class="value">${b.voltage?.toFixed(1)||'--'}<span class="unit">V</span></div></div>
                    <div class="metric"><div class="label">Current</div><div class="value">${b.current?.toFixed(1)||'--'}<span class="unit">A</span></div></div>
                    <div class="metric"><div class="label">Power</div><div class="value">${b.power?.toFixed(0)||'--'}<span class="unit">W</span></div></div>
                    <div class="metric"><div class="label">Temp</div><div class="value">${b.temperature?.toFixed(1)||'--'}<span class="unit">°C</span></div></div>
                </div>
                <div class="metrics-row">
                    <div class="metric"><div class="label">Energy</div><div class="value">${b.remaining_kwh?.toFixed(2)||'--'}<span class="unit">kWh</span></div></div>
                    <div class="metric"><div class="label">SOH</div><div class="value">${b.soh?.toFixed(0)||'--'}<span class="unit">%</span></div></div>
                    <div class="metric"><div class="label">Time to Low</div><div class="value" style="font-size:1.1em">${b.time_to_low||'--'}</div></div>
                    <div class="metric"><div class="label">Cell Δ</div><div class="value">${b.cell_delta?.toFixed(0)||'--'}<span class="unit">mV</span></div></div>
                </div>
                ${cells?`<div style="font-size:0.8em;color:#888;margin-top:10px;">Cells: ${b.cell_min?.toFixed(3)||'--'}V - ${b.cell_max?.toFixed(3)||'--'}V</div><div class="cell-grid">${cells}</div>`:''}
            </div>`;
        }
        function updateData() {
            fetch('/api/data').then(r=>r.json()).then(data=>{
                const batteries = data.batteries || [];
                document.getElementById('mqtt-status').className = 'status-dot ' + (data.mqtt_connected ? 'online' : 'offline');
                document.getElementById('battery-count').textContent = batteries.length + ' Batter' + (batteries.length===1?'y':'ies');
                const online = batteries.filter(b=>b.online);
                document.getElementById('total-kwh').textContent = online.reduce((s,b)=>s+(b.remaining_kwh||0),0).toFixed(2);
                document.getElementById('total-power').textContent = online.reduce((s,b)=>s+(b.power||0),0).toFixed(0);
                const totalCap = online.reduce((s,b)=>s+(b.full_capacity||0),0);
                const weightedSoc = totalCap ? online.reduce((s,b)=>s+(b.soc||0)*((b.full_capacity||0)/totalCap),0) : (online.reduce((s,b)=>s+(b.soc||0),0)/online.length);
                document.getElementById('avg-soc').textContent = online.length ? weightedSoc.toFixed(1) : '--';
                document.getElementById('online-count').textContent = online.length + '/' + batteries.length;
                document.getElementById('ttl-label').textContent = data.low_soc_cutoff ? `Time to ${data.low_soc_cutoff}%` : 'Time to Empty';
                document.getElementById('ttl-pack').textContent = data.pack_time_to_low || '--';
                document.getElementById('battery-grid').innerHTML = batteries.map(createBatteryCard).join('');
                document.getElementById('timestamp').textContent = batteries.length && batteries[0].timestamp ? new Date(batteries[0].timestamp).toLocaleString() : '--';
            }).catch(e=>console.error(e));
        }
        function createCircuitCard(c) {
            const bill = c.monthly_bill_estimate != null
                ? `<div class="bill-estimate"><div class="label">Projected Monthly Bill</div><div class="value">$${c.monthly_bill_estimate.toFixed(2)}</div></div>`
                : '';
            return `<div class="savings-col circuit-col">
                <div class="period"><span>${c.name}</span><span>${c.current_power!=null?c.current_power.toFixed(0)+' W':'--'}</span></div>
                ${bill}
                <div class="savings-metrics">
                    <div class="savings-metric cost"><div class="label">Today</div><div class="value">$${c.today.cost.toFixed(2)}</div></div>
                    <div class="savings-metric cost"><div class="label">This Month</div><div class="value">$${c.this_month.cost.toFixed(2)}</div></div>
                    <div class="savings-metric"><div class="label">Today kWh</div><div class="value">${c.today.kwh.toFixed(2)}</div></div>
                    <div class="savings-metric"><div class="label">Month kWh</div><div class="value">${c.this_month.kwh.toFixed(2)}</div></div>
                </div>
            </div>`;
        }
        function updateSavings() {
            fetch('/api/savings').then(r=>r.json()).then(data=>{
                document.getElementById('savings-section').style.display = data.enabled ? '' : 'none';
                document.getElementById('circuits-section').style.display = data.enabled && data.circuits && data.circuits.length ? '' : 'none';
                if (!data.enabled) return;
                document.getElementById('today-solar-savings').textContent = data.today.solar_savings.toFixed(2);
                document.getElementById('today-battery-savings').textContent = data.today.battery_savings.toFixed(2);
                document.getElementById('today-grid-cost').textContent = data.today.grid_cost.toFixed(2);
                document.getElementById('today-grid-charge-cost').textContent = data.today.grid_charge_cost.toFixed(2);
                document.getElementById('today-total-savings').textContent = data.today.total_savings.toFixed(2);
                document.getElementById('life-solar-savings').textContent = data.lifetime.solar_savings.toFixed(2);
                document.getElementById('life-battery-savings').textContent = data.lifetime.battery_savings.toFixed(2);
                document.getElementById('life-grid-cost').textContent = data.lifetime.grid_cost.toFixed(2);
                document.getElementById('life-grid-charge-cost').textContent = data.lifetime.grid_charge_cost.toFixed(2);
                document.getElementById('life-total-savings').textContent = data.lifetime.total_savings.toFixed(2);
                const cur = data.current;
                document.getElementById('in-pv-power').textContent = cur?.pv_power?.toFixed(0) ?? '--';
                document.getElementById('in-grid-power').textContent = cur?.grid_power?.toFixed(0) ?? '--';
                document.getElementById('in-battery-power').textContent = cur?.battery_power?.toFixed(0) ?? '--';
                document.getElementById('in-rate').textContent = cur?.rate?.toFixed(4) ?? '--';
                document.getElementById('in-grid-charging').textContent = cur ? (cur.grid_charging ? 'Yes' : 'No') : '--';
                document.getElementById('inputs-asof').textContent = cur?.timestamp ? '(' + new Date(cur.timestamp).toLocaleTimeString() + ')' : '';
                document.getElementById('circuits-grid').innerHTML = (data.circuits || []).map(createCircuitCard).join('');
            }).catch(e=>console.error(e));
        }
        updateData();
        updateSavings();
        setInterval(updateData, 5000);
        setInterval(updateSavings, 5000);
    </script>
</body>
</html>'''
