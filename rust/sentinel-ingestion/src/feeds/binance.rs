use super::{KlineClose, PriceTick};
use crate::CoreConfig;
use anyhow::Result;
use futures_util::StreamExt;
use serde::Deserialize;
use tokio::sync::broadcast;
use tokio_tungstenite::{connect_async, tungstenite};
use tracing::{info, warn, error};
use url::Url;

// ── Kline WebSocket deserialization ──────────────────────────────────────────

/// Combined-stream wrapper: {"stream":"btcusdt@kline_1m","data":{...}}
#[derive(Debug, Deserialize)]
struct KlineWsWrapper {
    data: KlineWsMessage,
}

#[derive(Debug, Deserialize)]
struct KlineWsMessage {
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "k")]
    kline: KlineWsData,
}

#[derive(Debug, Deserialize)]
struct KlineWsData {
    /// Kline open time (ms)
    #[serde(rename = "t")]
    open_time: u64,
    #[serde(rename = "o")]
    open: String,
    #[serde(rename = "h")]
    high: String,
    #[serde(rename = "l")]
    low: String,
    #[serde(rename = "c")]
    close: String,
    /// Base asset volume
    #[serde(rename = "v")]
    volume: String,
    /// true when the 1-minute candle has just closed
    #[serde(rename = "x")]
    is_closed: bool,
}

// ── Trade WebSocket deserialization ──────────────────────────────────────────

/// Binance WebSocket trade stream message
#[derive(Debug, Deserialize)]
struct BinanceTrade {
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "p")]
    price: String,
    #[serde(rename = "q")]
    quantity: String,
    #[serde(rename = "T")]
    trade_time: u64,
    #[serde(rename = "m")]
    is_buyer_maker: bool,
}

// ── REST: historical klines (warm-up) ────────────────────────────────────────

/// Fetch the most recent `limit` 1-minute klines for `symbol` from the
/// Binance Futures REST API (public endpoint, no auth required).
/// Used to warm up indicator buffers on startup.
fn get_base_url(config: &CoreConfig) -> &str {
    if config.exchange.primary.contains("testnet") {
        "https://testnet.binancefuture.com"
    } else {
        "https://fapi.binance.com"
    }
}

pub async fn fetch_historical_klines(
    config: &CoreConfig,
    symbol: &str,
    interval: &str,
    limit: u32,
) -> Result<Vec<KlineClose>> {
    let base_url = get_base_url(config);
    let url = format!(
        "{}/fapi/v1/klines?symbol={}&interval={}&limit={}",
        base_url, symbol, interval, limit
    );
    info!("Fetching {} historical {} klines for {} from {}…", limit, interval, symbol, base_url);

    let rows: Vec<Vec<serde_json::Value>> =
        reqwest::get(&url).await?.json().await?;

    let klines: Vec<KlineClose> = rows
        .iter()
        .map(|row| {
            let parse = |idx: usize| -> f64 {
                row.get(idx)
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0)
            };
            KlineClose {
                symbol: symbol.to_string(),
                timestamp_ms: row.first().and_then(|v| v.as_u64()).unwrap_or(0),
                open: parse(1),
                high: parse(2),
                low: parse(3),
                close: parse(4),
                volume: parse(5),
            }
        })
        .collect();

    info!("Fetched {} klines for {}", klines.len(), symbol);
    Ok(klines)
}

// ── REST: funding rate ──────────────────────────────────────────────────────

/// Funding rate data from Binance Futures premiumIndex endpoint
#[derive(Debug, Clone, Deserialize)]
pub struct FundingRateData {
    pub symbol: String,
    #[serde(rename = "lastFundingRate")]
    pub last_funding_rate: String,
    #[serde(rename = "nextFundingTime")]
    pub next_funding_time: u64,
    #[serde(rename = "markPrice")]
    pub mark_price: String,
}

/// Fetch current funding rate for a single symbol from Binance Futures.
pub async fn fetch_funding_rate(config: &CoreConfig, symbol: &str) -> Result<f64> {
    let base_url = get_base_url(config);
    let url = format!(
        "{}/fapi/v1/premiumIndex?symbol={}",
        base_url, symbol
    );
    let data: FundingRateData = reqwest::get(&url).await?.json().await?;
    Ok(data.last_funding_rate.parse().unwrap_or(0.0))
}

// ── REST: open interest ─────────────────────────────────────────────────────

/// Open interest data from Binance Futures
#[derive(Debug, Clone, Deserialize)]
pub struct OpenInterestData {
    #[serde(rename = "openInterest")]
    pub open_interest: String,
    pub symbol: String,
}

/// Fetch current open interest for a single symbol from Binance Futures.
pub async fn fetch_open_interest(config: &CoreConfig, symbol: &str) -> Result<(f64, f64)> {
    let base_url = get_base_url(config);
    // Current OI
    let url = format!(
        "{}/fapi/v1/openInterest?symbol={}",
        base_url, symbol
    );
    let data: OpenInterestData = reqwest::get(&url).await?.json().await?;
    let oi = data.open_interest.parse::<f64>().unwrap_or(0.0);

    // Also fetch mark price for USDT value
    let price_url = format!(
        "{}/fapi/v1/premiumIndex?symbol={}",
        base_url, symbol
    );
    let price_data: FundingRateData = reqwest::get(&price_url).await?.json().await?;
    let mark_price = price_data.mark_price.parse::<f64>().unwrap_or(0.0);

    Ok((oi, oi * mark_price))
}

// ── WebSocket: live 1-minute kline stream ────────────────────────────────────

/// Subscribe to Binance Futures @kline_1m for every symbol in config.
/// Sends a `KlineClose` on `tx` only when the candle has fully closed (x=true).
/// Reconnects automatically on disconnect.
pub async fn run_kline_feed(
    config: &CoreConfig,
    tx: broadcast::Sender<KlineClose>,
) -> Result<()> {
    let streams: Vec<String> = config
        .exchange
        .symbols
        .iter()
        .map(|s| format!("{}@kline_1m", s.to_lowercase()))
        .collect();

    let base_ws = if config.exchange.primary.contains("testnet") {
        "wss://stream.binancefuture.com"
    } else {
        "wss://fstream.binance.com"
    };

    // Combined-stream endpoint wraps each message as {"stream":"…","data":{…}}
    let ws_url = format!(
        "{}/stream?streams={}",
        base_ws,
        streams.join("/")
    );
    info!("Connecting to Binance kline WS: {}", ws_url);

    loop {
        match connect_async(Url::parse(&ws_url)?).await {
            Ok((ws_stream, _)) => {
                info!("✅ Binance kline WebSocket connected");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(tungstenite::Message::Text(text)) => {
                            if let Ok(wrapper) =
                                serde_json::from_str::<KlineWsWrapper>(&text)
                            {
                                let k = &wrapper.data.kline;
                                if k.is_closed {
                                    let kline = KlineClose {
                                        symbol: wrapper.data.symbol,
                                        timestamp_ms: k.open_time,
                                        open: k.open.parse().unwrap_or(0.0),
                                        high: k.high.parse().unwrap_or(0.0),
                                        low: k.low.parse().unwrap_or(0.0),
                                        close: k.close.parse().unwrap_or(0.0),
                                        volume: k.volume.parse().unwrap_or(0.0),
                                    };
                                    let _ = tx.send(kline);
                                }
                            }
                        }
                        Ok(tungstenite::Message::Ping(_)) => {} // handled by tungstenite
                        Err(e) => {
                            warn!("Kline WS message error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                warn!("Kline WS disconnected, reconnecting in 5s…");
            }
            Err(e) => {
                error!("Kline WS connection failed: {}", e);
            }
        }

        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

// ── WebSocket: live trade stream ──────────────────────────────────────────────

/// Connect to Binance Futures WebSocket and stream trades
pub async fn run_feed(
    config: &CoreConfig,
    tx: broadcast::Sender<PriceTick>,
) -> Result<()> {
    // Build combined stream URL: btcusdt@trade/ethusdt@trade
    let streams: Vec<String> = config
        .exchange
        .symbols
        .iter()
        .map(|s| format!("{}@trade", s.to_lowercase()))
        .collect();
    let stream_path = streams.join("/");

    let base_ws = if config.exchange.primary.contains("testnet") {
        "wss://stream.binancefuture.com/ws"
    } else {
        "wss://fstream.binance.com/ws"
    };

    let ws_url = format!("{}/{}", base_ws, stream_path);
    info!("Connecting to Binance WS: {}", ws_url);

    loop {
        match connect_async(Url::parse(&ws_url)?).await {
            Ok((ws_stream, _)) => {
                info!("✅ Binance WebSocket connected");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(tungstenite::Message::Text(text)) => {
                            if let Ok(trade) = serde_json::from_str::<BinanceTrade>(&text) {
                                let tick = PriceTick {
                                    symbol: trade.symbol,
                                    price: trade.price.parse().unwrap_or(0.0),
                                    volume: trade.quantity.parse().unwrap_or(0.0),
                                    timestamp_ms: trade.trade_time,
                                    is_buyer_maker: trade.is_buyer_maker,
                                    exchange: "binance_futures".to_string(),
                                };

                                // Non-blocking send — drops if no receivers
                                let _ = tx.send(tick);
                            }
                        }
                        Ok(tungstenite::Message::Ping(data)) => {
                            // Pong is handled automatically by tungstenite
                            let _ = data;
                        }
                        Err(e) => {
                            warn!("WebSocket message error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                warn!("WebSocket disconnected, reconnecting in 5s...");
            }
            Err(e) => {
                error!("WebSocket connection failed: {}", e);
            }
        }

        // Reconnect delay
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}
