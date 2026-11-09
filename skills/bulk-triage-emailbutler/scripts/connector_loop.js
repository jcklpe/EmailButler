/* A host-programmable connector bridge. Evaluating this trusted package file returns its API. */
(function () {
  const required = {
    gmail: ["gmail.profile", "gmail.labels", "gmail.read", "gmail.modify"],
    outlook: ["outlook.profile", "outlook.list", "outlook.move"],
  };
  const keep = new Set(["id", "message_id", "emailAddress", "email_address", "email", "address", "mail", "userPrincipalName", "user_principal_name", "labelIds", "label_ids", "labels", "name", "type", "value", "messages", "items", "internetMessageId", "internet_message_id", "parentFolderId", "parent_folder_id", "has_more", "@odata.nextLink", "data", "result", "profile", "message", "error", "code", "status", "status_code", "retry_after", "retryAfter", "headers", "Retry-After", "retry-after", "innerError", "innererror", "success", "ok", "message_ids", "next_page_token", "nextPageToken", "next_from_index"]);
  function clean(value) {
    if (Array.isArray(value)) return value.map(clean);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([key]) => keep.has(key)).map(([key, entry]) => [key, clean(entry)]));
    return value;
  }
  function normalize(raw) {
    if (!raw || typeof raw !== "object") return { error: { code: "unsupported_response" } };
    let value = raw;
    if (raw.structuredContent && typeof raw.structuredContent === "object") value = raw.structuredContent;
    else if (Array.isArray(raw.content)) {
      const candidates = raw.content.filter(part => part.type === "text").map(part => {
        try { return JSON.parse(part.text); } catch { return null; }
      }).filter(part => part && typeof part === "object");
      if (candidates.length !== 1) return { error: { code: "unstructured_tool_response" } };
      value = candidates[0];
    }
    if (raw.isError && !value.error) value = { error: { status: value.status, code: value.code || "connector_error", retry_after: value.retry_after, headers: value.headers } };
    return clean(value);
  }
  function quote(value) { return "'" + String(value).replaceAll("'", "'\"'\"'") + "'"; }
  async function run(options) {
    const { tools, bindings, provider, script, stateDir, runId, approvedRunId, mode = "run", inputPath, maxCalls = 1000, maxSeconds = 600, maxAttempts = 3, readLimit = 20, writeLimit = 100, previousCallSettled = false, onProgress = () => {}, cancelled = () => false } = options;
    if (!["run", "freeze"].includes(mode)) throw new Error("Unsupported execution mode");
    const allowed = mode === "freeze" ? { gmail: ["gmail.profile", "gmail.labels", "gmail.search"], outlook: ["outlook.profile", "outlook.list"] }[provider] : required[provider];
    if (!allowed || allowed.some(key => typeof bindings[key] !== "function")) throw new Error("This host lacks the required programmable connector bindings");
    if (!tools || typeof tools.exec_command !== "function" || typeof tools.write_stdin !== "function") throw new Error("This host lacks the required programmable process bridge");
    if (mode === "run" && (!/^R-[a-f0-9]{32}$/.test(runId) || approvedRunId !== runId)) throw new Error("The current approval must identify this exact run");
    if (mode === "freeze" && typeof inputPath !== "string") throw new Error("Read-only freeze needs a private selection input");
    for (const [name, value] of Object.entries({ maxCalls, maxSeconds, maxAttempts, readLimit, writeLimit })) if (!Number.isInteger(value) || value < 1) throw new Error("Invalid " + name);
    const args = ["python3", "-B", script, mode, "--state-dir", stateDir, "--max-calls", maxCalls, "--max-seconds", maxSeconds];
    if (mode === "run") args.push("--id", runId, "--approved-run-id", approvedRunId, "--max-attempts", maxAttempts, "--read-limit", readLimit, "--write-limit", writeLimit);
    else args.push("--input", inputPath);
    if (mode === "run" && previousCallSettled) args.push("--previous-call-settled");
    let response = await tools.exec_command({ cmd: args.map(quote).join(" "), tty: true, shell: "/bin/sh", login: false, yield_time_ms: 1000, max_output_tokens: 12000 });
    let session = response.session_id, buffer = response.output || "", pending = null;
    let calls = 0, heartbeat = null, finished = false;
    function scheduleProgress() {
      if (typeof setTimeout !== "function") return;
      heartbeat = setTimeout(async () => {
        try { await onProgress({ connectorCalls: calls, status: pending ? "waiting_for_connector" : "running" }); } catch {}
        if (!finished) scheduleProgress();
      }, 15000);
    }
    async function send(packet) {
      if (JSON.stringify(packet).length > 1000000) packet = { id: packet.id, result: { error: { code: "response_too_large" } } };
      response = await tools.write_stdin({ session_id: session, chars: JSON.stringify(packet) + "\n", yield_time_ms: 20, max_output_tokens: 12000 });
      buffer += response.output || "";
      if (response.session_id) session = response.session_id;
    }
    scheduleProgress();
    try {
      while (true) {
        const newline = buffer.indexOf("\n");
        if (newline < 0) {
          if (response.exit_code !== undefined) throw new Error("Executor ended without a summary");
          response = await tools.write_stdin({ session_id: session, chars: "", yield_time_ms: 1000, max_output_tokens: 12000 });
          buffer += response.output || "";
          if (buffer.length > 2000000) throw new Error("Executor protocol output exceeded its bound");
          continue;
        }
        const line = buffer.slice(0, newline).trim(); buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const message = JSON.parse(line);
        if (message.type === "summary") return message;
        if (message.type === "request" && Number.isInteger(message.id)) pending = message;
        if (!pending || !allowed.includes(message.operation)) throw new Error("Unexpected executor request");
        if (cancelled()) { await send({ id: message.id, cancelled: true }); pending = null; continue; }
        let result;
        try { result = normalize(await bindings[message.operation](message.arguments)); }
        catch (error) { result = clean({ error: { status: Number.isInteger(error?.status) ? error.status : undefined, code: error?.code || "transport_error", retry_after: error?.retry_after, headers: error?.headers } }); }
        calls += 1;
        await send({ id: message.id, result }); pending = null;
      }
    } catch (error) {
      if (pending && session) {
        try { await send({ id: pending.id, cancelled: true }); } catch {}
      }
      throw error;
    } finally {
      finished = true;
      if (heartbeat !== null && typeof clearTimeout === "function") clearTimeout(heartbeat);
    }
  }
  const api = { run, normalize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  return api;
})()
