/**
 * TradingView MCP Bridge — Node.js
 * Bridges the TradingView Desktop debug port to a local API.
 * 
 * Features:
 * - Symbol Change: Switch active symbol on chart.
 * - Take Screenshot: Capture chart for AI analysis.
 * - Inject Pine: Dynamically update Pine Script indicators.
 */

const http = require('http');
const fetch = require('node-fetch');
const WebSocket = require('ws');

const DEBUG_PORT = 9222;
const PORT = 3000;

const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    
    if (url.pathname === '/screenshot') {
        const screenshot = await takeScreenshot();
        res.writeHead(200, { 'Content-Type': 'image/png' });
        res.end(screenshot);
    } else if (url.pathname === '/symbol') {
        const symbol = url.searchParams.get('q');
        await changeSymbol(symbol);
        res.end(`Changed symbol to ${symbol}`);
    } else {
        res.end('TradingView MCP Bridge Active');
    }
});

async function getDebugTarget() {
    const res = await fetch(`http://localhost:${DEBUG_PORT}/json`);
    const targets = await res.json();
    return targets.find(t => t.url.includes('tradingview.com'));
}

async function takeScreenshot() {
    const target = await getDebugTarget();
    if (!target) return null;
    
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    return new Promise((resolve) => {
        ws.on('open', () => {
            ws.send(JSON.stringify({
                id: 1,
                method: 'Page.captureScreenshot',
                params: { format: 'png' }
            }));
        });
        ws.on('message', (data) => {
            const res = JSON.parse(data);
            if (res.id === 1) {
                resolve(Buffer.from(res.result.data, 'base64'));
                ws.close();
            }
        });
    });
}

async function changeSymbol(symbol) {
    const target = await getDebugTarget();
    if (!target) return;
    
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    ws.on('open', () => {
        // Simple injection to trigger symbol change via TradingView's JS API
        ws.send(JSON.stringify({
            id: 2,
            method: 'Runtime.evaluate',
            params: { expression: `window.chartWidgetCollection.activeChartWidget.value().model().mainSeries().setSymbol("${symbol}")` }
        }));
        setTimeout(() => ws.close(), 100);
    });
}

server.listen(PORT, () => {
    console.log(`🚀 TradingView MCP Bridge running on http://localhost:${PORT}`);
    console.log(`👉 Ensure TradingView is running with --remote-debugging-port=${DEBUG_PORT}`);
});
