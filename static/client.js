let socket;
let currentRoom = null;
let mySid = null;
let myHand = [];
let lastPublic = null;

const $ = (id) => document.getElementById(id);

function setConnStatus(text) {
  $("connStatus").textContent = text;
}

function pushError(msg) {
  const box = $("errors");
  const div = document.createElement("div");
  div.className = "item";
  div.textContent = `${new Date().toLocaleTimeString()}  ${msg}`;
  box.prepend(div);

  const toast = $("toast");
  if (toast) {
    const t = document.createElement("div");
    t.className = "toastMsg";
    t.textContent = msg;
    toast.appendChild(t);
    setTimeout(() => {
      try {
        toast.removeChild(t);
      } catch {}
    }, 3200);
  }
}

function cardSpan(c) {
  const div = document.createElement("div");
  div.className = "card";
  let rank = c.slice(0, -1).toUpperCase();
  const suit = c.slice(-1).toLowerCase();
  const suitMap = { s: "♠", h: "♥", d: "♦", c: "♣" };
  if (rank === "T") rank = "10";
  if (suit === "h" || suit === "d") div.classList.add("red");
  div.textContent = `${rank}${suitMap[suit] || suit}`;
  return div;
}

function miniCardSpan(c) {
  const div = document.createElement("div");
  div.className = "seatMiniCard";
  const suit = c.slice(-1);
  if (suit === "h" || suit === "d") div.classList.add("red");
  div.textContent = c.toUpperCase();
  return div;
}

function getMe(state) {
  return (state.players || []).find((p) => p.sid === mySid);
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function computeCallAmount(state, me) {
  if (!me) return 0;
  return Math.max(0, (state.currentBet || 0) - (me.bet || 0));
}

function computePotApprox(state) {
  // Pot is already tracked server-side.
  return state.pot || 0;
}

function setRaiseControls(state, me, isMyTurn) {
  const raiseTo = $("raiseTo");
  const slider = $("raiseSlider");

  if (!raiseTo || !slider) return;

  if (!me || !state.started) {
    raiseTo.value = 0;
    slider.min = 0;
    slider.max = 0;
    slider.value = 0;
    return;
  }

  const maxTo = (me.bet || 0) + (me.chips || 0);
  const minTo = Math.max(state.currentBet || 0, (state.currentBet || 0) + (state.minRaise || 0));

  slider.min = 0;
  slider.max = maxTo;
  slider.value = clamp(parseInt(raiseTo.value || `${minTo}`, 10) || minTo, 0, maxTo);

  // If not my turn, keep value but don't force.
  if (isMyTurn) {
    const v = clamp(parseInt(raiseTo.value || `${minTo}`, 10) || minTo, 0, maxTo);
    raiseTo.value = v;
    slider.value = v;
  }
}

function setQuickRaiseButtons(enabled) {
  ["btnRThirdPot", "btnRHalfPot", "btnR2x", "btnR3x", "btnR4x", "btnRPot", "btnRAllIn"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !enabled;
  });
}

function setBuyinButton(enabled) {
  const el = $("btnBuyin");
  if (el) el.disabled = !enabled;
}

function renderState(state) {
  lastPublic = state;

  $("handNo").textContent = state.handNo;
  $("stage").textContent = state.stage;
  $("pot").textContent = state.pot;
  $("currentBet").textContent = state.currentBet;
  $("minRaise").textContent = state.minRaise;

  const board = $("board");
  board.innerHTML = "";
  (state.board || []).forEach((c) => board.appendChild(cardSpan(c)));

  const handBox = $("myHand");
  handBox.innerHTML = "";
  (myHand || []).forEach((c) => handBox.appendChild(cardSpan(c)));

  // logs
  const logs = $("logs");
  logs.innerHTML = "";
  (state.logs || []).slice(-80).forEach((l) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<span class="code">${new Date(l.t * 1000).toLocaleTimeString()}</span>  ${l.msg}`;
    logs.appendChild(div);
  });

  // chat
  const chat = $("chat");
  chat.innerHTML = "";
  (state.chat || []).slice(-80).forEach((c) => {
    const div = document.createElement("div");
    div.className = "item";
    div.textContent = `${new Date(c.t * 1000).toLocaleTimeString()}  ${c.name}: ${c.text}`;
    chat.appendChild(div);
  });

  // players list
  const playersBox = $("players");
  if (playersBox) {
    playersBox.innerHTML = "";
    const players = (state.players || []).slice().sort((a, b) => a.seat - b.seat);

    const yes = () => `<span class="dot ok"></span>`;
    const no = () => `<span class="dot"></span>`;

    players.forEach((p) => {
      const isDealer = state.dealerSeat === p.seat;
      const isSB = state.sbSeat === p.seat;
      const isBB = state.bbSeat === p.seat;
      const isUTG = state.utgSeat === p.seat;
      const isTurn = state.started && state.actionSeat === p.seat;

      const row = document.createElement("div");
      row.className = `playerRow${isTurn ? " turn" : ""}`;
      row.innerHTML = `
        <div class="pName">${p.name}</div>
        <div class="pScore">${p.chips}</div>
        <div class="pNet">${p.net ?? (p.chips - (p.buyinTotal ?? 0))}</div>
        <div>${isDealer ? yes() : no()}</div>
        <div>${isSB ? yes() : no()}</div>
        <div>${isBB ? yes() : no()}</div>
        <div>${isUTG ? yes() : no()}</div>
      `;
      playersBox.appendChild(row);
    });
  }

  // turn hint + buttons
  const me = getMe(state);
  const isMyTurn = state.started && me && state.actionSeat === me.seat;

  const callAmt = computeCallAmount(state, me);
  const btnCall = $("btnCall");
  if (btnCall) {
    btnCall.textContent = callAmt > 0 ? `跟注 ${callAmt}` : "跟注(0)";
  }

  const btnCheck = $("btnCheck");
  if (btnCheck) {
    const canCheck = me ? me.bet === state.currentBet : false;
    btnCheck.textContent = canCheck ? "过牌" : "过牌(不可)";
  }

  const actionPlayer = (state.players || []).find((p) => p.seat === state.actionSeat);
  const actionName = actionPlayer ? actionPlayer.name : (state.actionSeat ? `座位 ${state.actionSeat}` : "-");

  $("turnHint").textContent =
    !currentRoom
      ? "先加入房间，然后即可看到桌面。"
      : !state.started
      ? "所有玩家点击「准备」后将自动开局。"
      : isMyTurn
      ? `轮到你行动：可以过牌 / 跟注${callAmt > 0 ? ` ${callAmt}` : ""} / 加注 / 弃牌。`
      : `当前轮到 ${actionName} 行动…`;

  $("btnReady").disabled = !currentRoom;
  $("btnStart").disabled = !currentRoom;
  $("btnChat").disabled = !currentRoom;

  // Buy-in is allowed only between hands (not started)
  setBuyinButton(!!currentRoom && !state.started);

  $("btnFold").disabled = !isMyTurn;
  $("btnCheck").disabled = !isMyTurn;
  $("btnCall").disabled = !isMyTurn;
  $("btnRaise").disabled = !isMyTurn;

  setQuickRaiseButtons(isMyTurn);
  setRaiseControls(state, me, isMyTurn);

  // Set ready button text
  if (me && me.ready) {
    $("btnReady").textContent = "取消准备";
  } else {
    $("btnReady").textContent = "准备";
  }

  // If current bet is 0, call button is effectively check; still ok.
}

function initSocket() {
  socket = io({ transports: ["websocket", "polling"] });

  socket.on("connect", () => {
    mySid = socket.id;
    setConnStatus(`connected (${mySid.slice(0, 6)})`);
  });

  socket.on("disconnect", () => {
    setConnStatus("disconnected");
  });

  socket.on("hello", () => {});

  socket.on("room_state", (state) => {
    renderState(state);
  });

  socket.on("private_state", (st) => {
    if (st && st.sid === mySid) {
      myHand = st.hand || [];
      if (lastPublic) renderState(lastPublic);
    }
  });

  socket.on("error", (e) => {
    pushError(e?.message || "error");
  });
}

function bindUI() {
  $("btnJoin").addEventListener("click", () => {
    const name = $("name").value.trim();
    const room = $("room").value.trim();
    if (!room) return;

    currentRoom = room;
    socket.emit("join", { room, name });

    $("btnJoin").disabled = true;
    $("btnLeave").disabled = false;
  });

  $("btnLeave").addEventListener("click", () => {
    if (!currentRoom) return;
    socket.emit("leave", { room: currentRoom });
    currentRoom = null;
    myHand = [];

    $("btnJoin").disabled = false;
    $("btnLeave").disabled = true;

    $("players").innerHTML = "";
    $("logs").innerHTML = "";
    $("chat").innerHTML = "";
    $("board").innerHTML = "";
    $("myHand").innerHTML = "";
    $("stage").textContent = "-";
    $("pot").textContent = "0";
    $("currentBet").textContent = "0";
    $("minRaise").textContent = "0";
  });

  $("btnReady").addEventListener("click", () => {
    if (!currentRoom) return;
    socket.emit("ready", { room: currentRoom });
  });

  $("btnStart").addEventListener("click", () => {
    if (!currentRoom) return;
    socket.emit("start", { room: currentRoom });
  });

  $("btnBuyin").addEventListener("click", () => {
    if (!currentRoom) return;
    socket.emit("buyin", { room: currentRoom, amount: 1000 });
  });

  $("btnFold").addEventListener("click", () => {
    socket.emit("action", { room: currentRoom, type: "fold" });
  });
  $("btnCheck").addEventListener("click", () => {
    socket.emit("action", { room: currentRoom, type: "check" });
  });
  $("btnCall").addEventListener("click", () => {
    socket.emit("action", { room: currentRoom, type: "call" });
  });
  $("btnRaise").addEventListener("click", () => {
    const v = parseInt($("raiseTo").value, 10);
    if (Number.isNaN(v)) {
      pushError("raise to 需要填数字");
      return;
    }
    socket.emit("action", { room: currentRoom, type: "raise", amount: v });
  });

  const slider = $("raiseSlider");
  if (slider) {
    slider.addEventListener("input", () => {
      $("raiseTo").value = slider.value;
    });
  }

  $("raiseTo").addEventListener("input", () => {
    const v = parseInt($("raiseTo").value || "0", 10);
    if (!Number.isNaN(v) && slider) slider.value = v;
  });

  $("btnR2x").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const base = Math.max(lastPublic.currentBet || 0, lastPublic.minRaise || 0);
    const to = base * 2;
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });
  $("btnR3x").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const base = Math.max(lastPublic.currentBet || 0, lastPublic.minRaise || 0);
    const to = base * 3;
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });
  $("btnR4x").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const base = Math.max(lastPublic.currentBet || 0, lastPublic.minRaise || 0);
    const to = base * 4;
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });

  $("btnRThirdPot").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const pot = computePotApprox(lastPublic);
    const to = (lastPublic.currentBet || 0) + Math.floor(pot / 3);
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });

  $("btnRHalfPot").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const pot = computePotApprox(lastPublic);
    const to = (lastPublic.currentBet || 0) + Math.floor(pot / 2);
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });
  $("btnRPot").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    // Rough pot-sized raise target: currentBet + pot + call
    const call = computeCallAmount(lastPublic, me);
    const to = (lastPublic.currentBet || 0) + computePotApprox(lastPublic) + call;
    $("raiseTo").value = clamp(to, 0, me.bet + me.chips);
    if (slider) slider.value = $("raiseTo").value;
  });
  $("btnRAllIn").addEventListener("click", () => {
    if (!lastPublic) return;
    const me = getMe(lastPublic);
    if (!me) return;
    const to = (me.bet || 0) + (me.chips || 0);
    $("raiseTo").value = to;
    if (slider) slider.value = to;
  });

  $("btnChat").addEventListener("click", () => {
    const text = $("chatText").value;
    $("chatText").value = "";
    socket.emit("chat", { room: currentRoom, text });
  });

  $("chatText").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("btnChat").click();
  });
}

initSocket();
bindUI();
