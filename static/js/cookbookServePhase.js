// Serve-task phase detection. Mirrors routes/cookbook_helpers.py
// _parse_serve_phase. The llama-server branch must not treat GET /v1/models
// 200 as ready: that route answers while weights are still loading.

function llamaServerHttpReady(flat) {
  // /v1/models is 200 during load. /health stays 503 until `model loaded`.
  if (/\bmodel loaded\b/i.test(flat || '')) return true;
  const accesses = [...(flat || '').matchAll(/(?:GET|POST)\s+\/([^\s]*)\s+HTTP\/[\d.]+"\s*(\d{3})/g)];
  for (const m of accesses) {
    const norm = m[1].split('?')[0].replace(/^\/+|\/+$/g, '');
    if ((norm === 'health' || norm === 'v1/health') && m[2] === '200') return true;
  }
  for (const m of accesses) {
    const norm = m[1].split('?')[0].replace(/^\/+|\/+$/g, '');
    if (norm === 'v1/models' || norm === 'models') continue;
    if (m[2].startsWith('2')) return true;
  }
  return false;
}

export function parseServePhase(snapshot) {
  if (!snapshot) return {};
  // Strip newlines so tmux line-wrapping doesn't break regex matching
  const flat = snapshot.replace(/\s+/g, ' ');
  const loadMatches = [...flat.matchAll(/Loading safetensors.*?(\d+)%/g)];
  // "Downloading (incomplete total...)" tracks real aggregate bytes; prefer it
  // over "Fetching N files" which only counts fully-closed files and lags badly
  // with hf_transfer's parallel-chunk strategy (often sits at 0/N for most of the run).
  const downloadingMatches = [...flat.matchAll(/Downloading.*?(\d+)%/g)];
  const fetchingMatches = [...flat.matchAll(/Fetching.*?(\d+)%/g)];
  const dlMatches = downloadingMatches.length ? downloadingMatches : fetchingMatches;
  // "Avg generation throughput: X tokens/s, Running: N reqs"
  const tpsMatches = [...flat.matchAll(/(?:Avg )?generation throughput:\s*([\d.]+)\s*tokens\/s.*?Running:\s*(\d+)\s*reqs/g)];

  // Throughput FIRST — its log line contains "GPU KV cache usage" which would
  // otherwise false-match the warmup check
  if (tpsMatches.length) {
    const m = tpsMatches[tpsMatches.length - 1];
    const tps = parseFloat(m[1]);
    const reqs = parseInt(m[2]);
    return {
      phase: reqs > 0 ? `${m[1]} tok/s` : 'idle',
      status: 'ready',
      tps,
      reqs,
    };
  }
  if (flat.includes('Application startup complete')) {
    return { phase: 'ready', status: 'ready' };
  }
  if (/Ollama API ready on port\s+\d+/i.test(flat)) {
    return { phase: 'ready', status: 'ready' };
  }
  const llamaBuildMatches = [...flat.matchAll(/\[\s*(\d{1,3})%\]\s*(?:Building|Linking)/gi)];
  if (llamaBuildMatches.length) {
    const pct = Math.min(100, parseInt(llamaBuildMatches[llamaBuildMatches.length - 1][1], 10));
    return { phase: `building llama.cpp ${pct}%`, status: 'running', pct };
  }
  if (/Native llama-server not found|building from source/i.test(flat)) {
    if (/Cloning into ['"]?llama\.cpp/i.test(flat) && !/Receiving objects:\s*100%/i.test(flat)) {
      return { phase: 'cloning llama.cpp', status: 'running' };
    }
    if (/Configuring incomplete|CMake Error/i.test(flat)) {
      return {};
    }
    if (/CMAKE_BUILD_TYPE|Detecting CXX|Found Threads|Including CPU backend|CUDA nvcc found|building llama-server/i.test(flat)) {
      return { phase: 'configuring llama.cpp', status: 'running' };
    }
    return { phase: 'building llama.cpp', status: 'running' };
  }
  // HTTP access logs mean most servers are up. llama-server is the exception:
  // GET /v1/models returns 200 while weights are still loading.
  if (/llama[-_]server/i.test(flat)) {
    if (llamaServerHttpReady(flat)) {
      return { phase: 'idle', status: 'ready' };
    }
  } else if (/(?:GET|POST)\s+\/[^\s]*\s+HTTP\/[\d.]+"\s*\d{3}/.test(flat)) {
    return { phase: 'idle', status: 'ready' };
  }
  if (flat.includes('Loading weights took')) {
    return { phase: 'initializing', status: 'running' };
  }
  // "GPU KV cache" alone (during allocation) — not "GPU KV cache usage" (runtime log)
  if (flat.includes('GPU KV cache') && !flat.includes('GPU KV cache usage')) {
    return { phase: 'warming up', status: 'running' };
  }
  if (loadMatches.length) {
    const pct = parseInt(loadMatches[loadMatches.length - 1][1]);
    return { phase: `loading ${pct}%`, status: 'running', pct };
  }
  if (dlMatches.length) {
    const pct = parseInt(dlMatches[dlMatches.length - 1][1]);
    return { phase: `downloading ${pct}%`, status: 'running', pct };
  }
  return {};
}
