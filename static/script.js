// â”€â”€â”€ STATE â”€â”€â”€
let sidebarOpen = true;
let currentPanel = null;
let isTyping = false;
const models = [
  "O'zbekiston Respublikasi kodekslari",
  'Fuqarolik kodeksi',
  'Mehnat kodeksi',
  'Jinoyat kodeksi'
];
let modelIdx = 0;
const CHAT_API_URL = '/api/chat';
const SPEECH_TO_TEXT_TIMEOUT_MS = 35000;
let mediaRecorder = null;
let recordedChunks = [];
let currentSpeechAudio = null;
let speechToTextController = null;
let speechToTextTimeoutId = null;
let isTranscribing = false;
let isListening = false;
let isSpeaking = false;
let voiceModeActive = false;
let shouldSpeakNextAnswer = false;
let voiceBaseText = '';
let cancelVoiceRequested = false;
const CHAT_STORAGE_KEY = 'huquq-ai-current-chat';
let chatMessages = [];

// â”€â”€â”€ SIDEBAR â”€â”€â”€
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.getElementById('sidebar').classList.toggle('collapsed', !sidebarOpen);
}

// â”€â”€â”€ THEME â”€â”€â”€
function toggleTheme() {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === 'dark' ? 'light' : 'dark';
}

// â”€â”€â”€ MODEL â”€â”€â”€
function updateModelUI() {
  const currentModel = models[modelIdx];
  document.getElementById('modelName').textContent = currentModel;
  document.getElementById('modelBadgeName').textContent = currentModel;
  document.querySelectorAll('.model-option').forEach((option, idx) => {
    option.classList.toggle('active', idx === modelIdx);
  });
}

function toggleModelMenu(event) {
  event.stopPropagation();
  document.getElementById('modelDropdown').classList.toggle('open');
}

function selectModel(index) {
  modelIdx = index;
  updateModelUI();
  document.getElementById('modelDropdown').classList.remove('open');
}

// â”€â”€â”€ PANELS â”€â”€â”€
function openPanel(name) {
  closePanel();
  const map = { search: 'searchPanel', tools: 'toolsPanel', files: 'filesPanel', stats: 'statsPanel' };
  const panelId = map[name];
  if (!panelId) return;
  document.getElementById('panelBackdrop').classList.add('visible');
  document.getElementById(panelId).classList.add('visible');
  currentPanel = panelId;
}

function closePanel() {
  if (currentPanel) {
    document.getElementById(currentPanel).classList.remove('visible');
    currentPanel = null;
  }
  document.getElementById('panelBackdrop').classList.remove('visible');
}

// â”€â”€â”€ SETTINGS â”€â”€â”€
function openSettings() { document.getElementById('settingsModal').classList.add('visible'); }
function closeSettings() { document.getElementById('settingsModal').classList.remove('visible'); }
function closeSettingsOutside(e) { if (e.target === e.currentTarget) closeSettings(); }

// â”€â”€â”€ TABS â”€â”€â”€
function switchTab(btn, tabId) {
  btn.closest('.panel-body').querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const allTabs = ['tools-tab', 'templates-tab', 'petition-tab'];
  allTabs.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', id !== tabId);
  });
}

// â”€â”€â”€ CHAT HISTORY â”€â”€â”€
function loadChat(el) {
  document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');
}

function showWelcomeState() {
  document.getElementById('messagesContainer').innerHTML = '';
  document.getElementById('messagesContainer').classList.add('hidden');
  document.getElementById('welcomeState').style.display = '';
}

function showMessagesState() {
  document.getElementById('welcomeState').style.display = 'none';
  document.getElementById('messagesContainer').classList.remove('hidden');
}

function getStoredChatMessages() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CHAT_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(message =>
      message &&
      (message.role === 'user' || message.role === 'ai') &&
      typeof message.content === 'string'
    );
  } catch (_) {
    return [];
  }
}

function saveStoredChatMessages() {
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatMessages.slice(-100)));
  } catch (_) {}
}

function clearStoredChatMessages() {
  chatMessages = [];
  try {
    localStorage.removeItem(CHAT_STORAGE_KEY);
  } catch (_) {}
}

function restoreChatOnLoad() {
  chatMessages = getStoredChatMessages();

  if (!chatMessages.length) {
    showWelcomeState();
    return;
  }

  const messages = [...chatMessages];
  document.getElementById('messagesContainer').innerHTML = '';
  showMessagesState();
  messages.forEach(message => addMessage(message.role, message.content, {
    persist: false,
    time: message.time
  }));
  chatMessages = messages;
  scrollToBottom();
}

// â”€â”€â”€ TEXTAREA AUTO-RESIZE â”€â”€â”€
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  document.getElementById('sendBtn').disabled = el.value.trim() === '';
}

// â”€â”€â”€ KEY HANDLER â”€â”€â”€
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function toggleVoiceInput() {
  if (isSpeaking) {
    stopSpeech();
    return;
  }

  if (isTranscribing) {
    cancelVoiceInput();
    return;
  }

  if (isListening) {
    finishVoiceInput();
    return;
  }

  startVoiceInput();
}

function stopActiveVoice() {
  cancelVoiceInput();
}

async function startVoiceInput() {
  if (isTyping) return;

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert('Brauzeringiz audio yozishni qo\'llab-quvvatlamaydi. Chrome yoki Edge brauzerida sinab ko\'ring.');
    return;
  }

  stopSpeech();
  cancelVoiceRequested = false;
  voiceModeActive = true;
  voiceBaseText = document.getElementById('chatInput').value.trim();

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      setVoiceListeningState(false);
      if (cancelVoiceRequested) {
        recordedChunks = [];
        return;
      }
      await transcribeRecordedAudio();
    };

    mediaRecorder.start();
    setVoiceListeningState(true);
    updateVoiceStatus('Tinglayapman...', 'Tugatish uchun mikrofonni, bekor qilish uchun Stop tugmasini bosing.');
  } catch (error) {
    console.warn('MediaRecorder start error:', error);
    updateVoiceStatus('Mikrofon ochilmadi', 'Brauzer ruxsatini tekshiring');
    setVoiceListeningState(false);
  }
}

function finishVoiceInput() {
  if (mediaRecorder && isListening && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    updateVoiceStatus('Ovoz matnga aylantirilmoqda...', 'Python STT ishlayapti');
    return;
  }
  setVoiceListeningState(false);
}

function stopVoiceInput() {
  finishVoiceInput();
}

function cancelVoiceInput(event, options = {}) {
  if (event) event.stopPropagation();

  const silent = options.silent === true;
  cancelVoiceRequested = true;
  voiceModeActive = false;
  shouldSpeakNextAnswer = false;
  recordedChunks = [];

  if (speechToTextController) {
    speechToTextController.abort();
    speechToTextController = null;
  }

  if (speechToTextTimeoutId) {
    clearTimeout(speechToTextTimeoutId);
    speechToTextTimeoutId = null;
  }

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try {
      mediaRecorder.stop();
    } catch (_) {}
  }

  stopSpeech();
  setVoiceListeningState(false);
  setVoiceProcessingState(false);

  if (!silent) {
    updateVoiceStatus('Ovozli jarayon bekor qilindi', 'Textga aylantirish to\'xtatildi.');
    showVoiceStatus(true, 'cancelled');
    setTimeout(() => {
      if (cancelVoiceRequested && !isListening && !isTranscribing && !isSpeaking) {
        showVoiceStatus(false);
      }
    }, 1200);
  } else {
    showVoiceStatus(false);
  }
}

async function transcribeRecordedAudio() {
  if (!recordedChunks.length || cancelVoiceRequested) return;

  const blob = new Blob(recordedChunks, { type: mediaRecorder?.mimeType || 'audio/webm' });
  recordedChunks = [];
  speechToTextController = new AbortController();
  let speechToTextTimedOut = false;
  isTranscribing = true;

  try {
    setVoiceProcessingState(true);
    updateVoiceStatus('Ovoz matnga aylantirilmoqda...', 'Python STT ishlayapti. Stop bosilsa jarayon bekor qilinadi.');
    showVoiceStatus(true, 'processing');

    const formData = new FormData();
    formData.append('audio', blob, 'question.webm');

    speechToTextTimeoutId = setTimeout(() => {
      speechToTextTimedOut = true;
      if (speechToTextController) {
        speechToTextController.abort();
      }
    }, SPEECH_TO_TEXT_TIMEOUT_MS);

    const response = await fetch('/api/speech-to-text', {
      method: 'POST',
      body: formData,
      signal: speechToTextController.signal
    });

    if (speechToTextTimeoutId) {
      clearTimeout(speechToTextTimeoutId);
      speechToTextTimeoutId = null;
    }

    const data = await response.json();

    if (cancelVoiceRequested) return;

    if (!response.ok || !data.text) {
      throw new Error(data.detail || 'Ovozdan matn olishda xatolik yuz berdi.');
    }

    const input = document.getElementById('chatInput');
    input.value = [voiceBaseText, data.text].filter(Boolean).join(' ');
    autoResize(input);
    shouldSpeakNextAnswer = true;
    showVoiceStatus(false);
    isTranscribing = false;
    speechToTextController = null;
    setVoiceProcessingState(false);
    sendMessage();
  } catch (error) {
    if (speechToTextTimeoutId) {
      clearTimeout(speechToTextTimeoutId);
      speechToTextTimeoutId = null;
    }

    if (error.name === 'AbortError' && speechToTextTimedOut) {
      updateVoiceStatus('Ovoz matnga aylantirilmadi', 'STT 35 soniyada javob bermadi. Qisqaroq gapirib qayta urinib ko\'ring.');
      showVoiceStatus(true, 'cancelled');
      addMessage('ai', 'Ovoz matnga aylantirilmadi: STT juda uzoq vaqt javob bermadi. Qisqaroq gapirib qayta urinib ko\'ring yoki savolni matn qilib yozing.');
      return;
    }

    if (error.name === 'AbortError' || cancelVoiceRequested) {
      return;
    }

    console.error(error);
    showVoiceStatus(false);
    addMessage('ai', `Ovozdan matn olishda xatolik yuz berdi.\n\n**Tekshiring:**\n- Python STT kutubxonalari o'rnatilganmi\n- Mikrofon ruxsati berilganmi\n- Audio juda qisqa emasmi`);
  } finally {
    isTranscribing = false;
    speechToTextController = null;
    if (speechToTextTimeoutId) {
      clearTimeout(speechToTextTimeoutId);
      speechToTextTimeoutId = null;
    }
    setVoiceProcessingState(false);
    if (cancelVoiceRequested && !isListening && !isSpeaking) {
      setTimeout(() => {
        if (cancelVoiceRequested && !isListening && !isTranscribing && !isSpeaking) {
          showVoiceStatus(false);
        }
      }, 400);
    }
  }
}

function setVoiceListeningState(active) {
  isListening = active;
  const voiceBtn = document.getElementById('voiceBtn');
  const voiceStatus = document.getElementById('voiceStatus');
  const inputBox = document.querySelector('.input-box');

  if (voiceBtn) {
    voiceBtn.classList.toggle('listening', active);
    voiceBtn.innerHTML = active ? '<i class="bi bi-stop-fill"></i>' : '<i class="bi bi-mic"></i>';
    voiceBtn.title = active ? 'Yozishni yakunlab textga aylantirish' : 'Ovozli savol';
  }

  showVoiceStatus(active, active ? 'listening' : '');

  if (inputBox) {
    inputBox.classList.toggle('voice-active', active);
  }
}

function setVoiceProcessingState(active) {
  isTranscribing = active;
  const voiceBtn = document.getElementById('voiceBtn');
  const inputBox = document.querySelector('.input-box');

  if (voiceBtn) {
    voiceBtn.classList.toggle('processing', active);
    if (active) {
      voiceBtn.innerHTML = '<i class="bi bi-stop-fill"></i>';
      voiceBtn.title = 'Textga aylantirishni to\'xtatish';
    } else if (!isListening && !isSpeaking) {
      voiceBtn.innerHTML = '<i class="bi bi-mic"></i>';
      voiceBtn.title = 'Ovozli savol';
    }
  }

  if (inputBox) {
    inputBox.classList.toggle('voice-processing', active);
  }
}

function setVoiceSpeakingState(active) {
  isSpeaking = active;
  const voiceBtn = document.getElementById('voiceBtn');
  const inputBox = document.querySelector('.input-box');

  if (voiceBtn) {
    voiceBtn.classList.toggle('speaking', active);
    voiceBtn.innerHTML = active ? '<i class="bi bi-volume-up-fill"></i>' : '<i class="bi bi-mic"></i>';
    voiceBtn.title = active ? 'Ovozni to\'xtatish' : 'Ovozli savol';
  }

  if (inputBox) {
    inputBox.classList.toggle('voice-speaking', active);
  }
}

function showVoiceStatus(visible, mode = '') {
  const voiceStatus = document.getElementById('voiceStatus');
  if (!voiceStatus) return;

  voiceStatus.classList.toggle('visible', visible);
  voiceStatus.classList.toggle('speaking', mode === 'speaking');
  voiceStatus.classList.toggle('processing', mode === 'processing');
  voiceStatus.classList.toggle('listening', mode === 'listening');
  voiceStatus.classList.toggle('cancelled', mode === 'cancelled');
}

function updateVoiceStatus(title, subtitle) {
  const status = document.getElementById('voiceStatus');
  if (!status) return;

  const titleEl = status.querySelector('.voice-status-title');
  const subEl = status.querySelector('.voice-status-sub');
  if (titleEl) titleEl.textContent = title;
  if (subEl) subEl.textContent = subtitle;
}

async function speakAnswer(text) {
  const clean = cleanTextForSpeech(text);
  if (!clean) return;

  stopSpeech();

  try {
    updateVoiceStatus('Sardor ovozi javob bermoqda...', 'To\'xtatish uchun shu panel yoki mikrofonni bosing');
    showVoiceStatus(true, 'speaking');
    setVoiceSpeakingState(true);

    const response = await fetch('/api/text-to-speech', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: clean })
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || 'TTS xatoligi');
    }

    const audioBlob = await response.blob();
    const audioUrl = URL.createObjectURL(audioBlob);
    currentSpeechAudio = new Audio(audioUrl);
    currentSpeechAudio.onended = () => {
      URL.revokeObjectURL(audioUrl);
      setVoiceSpeakingState(false);
      showVoiceStatus(false);
      currentSpeechAudio = null;
    };
    currentSpeechAudio.onerror = () => {
      URL.revokeObjectURL(audioUrl);
      setVoiceSpeakingState(false);
      showVoiceStatus(false);
      currentSpeechAudio = null;
    };
    await currentSpeechAudio.play();
  } catch (error) {
    console.error(error);
    setVoiceSpeakingState(false);
    showVoiceStatus(false);
    addMessage('ai', 'Ovozli javobni Sardor ovozida yaratib bo\'lmadi. `edge-tts` o\'rnatilgani va internet mavjudligini tekshiring.');
  }
}

function stopSpeech() {
  if (currentSpeechAudio) {
    currentSpeechAudio.pause();
    currentSpeechAudio.currentTime = 0;
    currentSpeechAudio = null;
  }
  setVoiceSpeakingState(false);
  if (!isListening && !isTranscribing) {
    showVoiceStatus(false);
  }
}

function cleanTextForSpeech(text) {
  return text
    .replace(/\[\[SOURCE_LINK:https?:\/\/[^\]]+\]\]/g, 'Manba')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/[*_#`>-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// â”€â”€â”€ USE PROMPT â”€â”€â”€
function usePrompt(text) {
  const input = document.getElementById('chatInput');
  input.value = text;
  autoResize(input);
  input.focus();
  closePanel();
}

function getPetitionFields() {
  return {
    organization: document.getElementById('petitionTo').value.trim(),
    recipient: document.getElementById('petitionRecipient').value.trim(),
    from: document.getElementById('petitionFrom').value.trim(),
    address: document.getElementById('petitionAddress').value.trim(),
    subject: document.getElementById('petitionSubject').value.trim(),
    situation: document.getElementById('petitionSituation').value.trim(),
    request: document.getElementById('petitionRequest').value.trim(),
    attachments: document.getElementById('petitionAttachments').value.trim()
  };
}

function buildPetitionText(data) {
  const today = new Date().toLocaleDateString('uz-UZ');
  const organization = data.organization || '[tashkilot nomi]';
  const recipient = data.recipient || '[rahbar F.I.Sh.]';
  const from = data.from || '[ariza beruvchi]';
  const address = data.address || '[manzil]';
  const subject = data.subject || '[mavzu]';
  const situation = data.situation || '[holat tafsiloti]';
  const request = data.request || '[talab]';
  const attachments = data.attachments || '[ilovalar]';

  return `${organization}
${recipient}

${address} manzilida yashovchi
${from} dan

ARIZA

Mavzu: ${subject}

Men, ${from}, quyidagi masala bo'yicha murojaat qilaman:
${situation}

Shu asosda sizdan quyidagilarni so'rayman:
${request}

Ilovalar: ${attachments}

Sana: ${today}
Ariza beruvchi: ${from}`;
}

function buildPetitionPreviewHtml(data) {
  const organization = data.organization || '____________________________';
  const recipient = data.recipient || '____________________________';
  const from = data.from || '____________________________';
  const address = data.address || '____________________________';
  const subject = data.subject || '____________________________';
  const situation = data.situation || '____________________________________________';
  const request = data.request || '____________________________________________';
  const attachments = data.attachments || '____________________________________________';
  const today = new Date().toLocaleDateString('uz-UZ');

  return `
    <div class="petition-doc">
      <div class="petition-doc-header">
        <div class="petition-doc-recipient">
          <p>${escapeHtml(organization)}</p>
          <p>${escapeHtml(recipient)}</p>
          <p>${escapeHtml(address)} manzilida yashovchi</p>
          <p>${escapeHtml(from)} dan</p>
        </div>
      </div>
      <div class="petition-doc-title">Ariza</div>
      <div class="petition-doc-body">
        <p>Men, ${escapeHtml(from)}, ${escapeHtml(subject.toLowerCase())} masalasi yuzasidan ushbu ariza bilan murojaat qilaman.</p>
        <p>${escapeHtml(situation)}</p>
        <p>Shu munosabat bilan sizdan quyidagilarni ko'rib chiqishingizni so'rayman: ${escapeHtml(request)}</p>
      </div>
      <div class="petition-doc-attachments"><strong>Ilovalar:</strong> ${escapeHtml(attachments)}</div>
      <div class="petition-doc-footer">
        <div class="petition-doc-signature">
          <strong>Ariza beruvchi:</strong>
          <span class="petition-doc-line"></span>
          <span>${escapeHtml(from)}</span>
        </div>
        <div class="petition-doc-date">
          <span>${escapeHtml(today)}</span>
        </div>
      </div>
    </div>
  `;
}

function buildPetitionDocxHtml(data) {
  const organization = data.organization || '____________________________';
  const recipient = data.recipient || '____________________________';
  const from = data.from || '____________________________';
  const address = data.address || '____________________________';
  const subject = data.subject || '____________________________';
  const situation = data.situation || '____________________________________________';
  const request = data.request || '____________________________________________';
  const attachments = data.attachments || '____________________________________________';
  const today = new Date().toLocaleDateString('uz-UZ');

  return `
<!DOCTYPE html>
<html lang="uz">
<head>
  <meta charset="UTF-8">
  <title>Ariza</title>
  <style>
    body {
      font-family: "Times New Roman", Times, serif;
      color: #000;
      margin: 0;
      padding: 24px;
      background: #fff;
    }
    .page {
      border: 2px solid #000;
      padding: 34px 38px 60px;
      min-height: 980px;
    }
    .page-inner {
      border: 1px solid #000;
      min-height: 930px;
      padding: 34px 42px 50px;
    }
    .recipient {
      width: 48%;
      margin-left: auto;
      font-size: 15px;
      font-weight: bold;
      line-height: 1.4;
      text-align: left;
    }
    .recipient p {
      margin: 0 0 4px;
    }
    .title {
      text-align: center;
      font-size: 28px;
      font-weight: bold;
      margin: 70px 0 42px;
    }
    .body {
      font-size: 16px;
      line-height: 1.45;
      text-align: justify;
    }
    .body p {
      margin: 0 0 16px;
      text-indent: 42px;
    }
    .attachments {
      margin-top: 18px;
      font-size: 16px;
    }
    .footer {
      margin-top: 68px;
      font-size: 16px;
    }
    .signature-row {
      margin-bottom: 18px;
    }
  </style>
</head>
<body>
  <div class="page">
    <div class="page-inner">
      <div class="recipient">
        <p>${escapeHtml(organization)}</p>
        <p>${escapeHtml(recipient)}</p>
        <p>${escapeHtml(address)} manzilida yashovchi</p>
        <p>${escapeHtml(from)} dan</p>
      </div>
      <div class="title">Ariza</div>
      <div class="body">
        <p>Men, ${escapeHtml(from)}, ${escapeHtml(subject.toLowerCase())} masalasi yuzasidan ushbu ariza bilan murojaat qilaman.</p>
        <p>${escapeHtml(situation)}</p>
        <p>Shu munosabat bilan sizdan quyidagilarni ko'rib chiqishingizni so'rayman: ${escapeHtml(request)}</p>
      </div>
      <div class="attachments"><strong>Ilovalar:</strong> ${escapeHtml(attachments)}</div>
      <div class="footer">
        <div class="signature-row"><strong>Ariza beruvchi:</strong> ${escapeHtml(from)}</div>
        <div><strong>Sana:</strong> ${escapeHtml(today)}</div>
      </div>
    </div>
  </div>
</body>
</html>`;
}

function generatePetition() {
  const data = getPetitionFields();
  document.getElementById('petitionPreview').innerHTML = buildPetitionPreviewHtml(data);
  return buildPetitionText(data);
}

function useGeneratedPetition() {
  const petition = generatePetition();
  const input = document.getElementById('chatInput');
  input.value = petition;
  autoResize(input);
  input.focus();
  closePanel();
}

function copyGeneratedPetition() {
  const petition = generatePetition();
  navigator.clipboard.writeText(petition).catch(() => {});
}

function downloadPetitionDocx() {
  const data = getPetitionFields();
  generatePetition();

  if (!window.htmlDocx) {
    alert('DOCX yuklab olish moduli topilmadi.');
    return;
  }

  const blob = window.htmlDocx.asBlob(buildPetitionDocxHtml(data));
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const safeName = (data.subject || 'ariza')
    .toLowerCase()
    .replace(/[^a-z0-9а-яё\u0400-\u04FF]+/gi, '-')
    .replace(/^-+|-+$/g, '') || 'ariza';

  link.href = url;
  link.download = `${safeName}.docx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// â”€â”€â”€ NEW CHAT â”€â”€â”€
function startNewChat() {
  cancelVoiceInput(null, { silent: true });
  stopSpeech();
  clearStoredChatMessages();
  showWelcomeState();
  document.getElementById('chatInput').value = '';
  document.getElementById('sendBtn').disabled = true;
  document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
}

// â”€â”€â”€ SEND MESSAGE â”€â”€â”€
async function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || isTyping) return;
  const speakThisReply = shouldSpeakNextAnswer || voiceModeActive;
  shouldSpeakNextAnswer = false;
  voiceModeActive = false;
  if (isListening || isTranscribing) {
    cancelVoiceInput(null, { silent: true });
  }
  stopSpeech();

  // Show chat
  showMessagesState();

  // Add user message
  addMessage('user', text);
  input.value = '';
  autoResize(input);
  document.getElementById('sendBtn').disabled = true;

  // Scroll
  scrollToBottom();

  // Typing indicator
  isTyping = true;
  const typingId = addTypingIndicator();

  try {
    const reply = await requestAiAnswer(text);
    removeTypingIndicator(typingId);
    addStreamingMessage(reply, { speak: speakThisReply });
  } catch (error) {
    console.error(error);
    removeTypingIndicator(typingId);
    addMessage('ai', `Kechirasiz, AI serverdan javob olishda xatolik yuz berdi.\n\n**Tekshirib ko'ring:**\n- Backend server ishga tushganmi: \`python app.py\`\n- Ollama modeli ishlayaptimi\n- \`vektor_baza\` papkasi mavjudmi`);
  } finally {
    isTyping = false;
    input.focus();
  }
}

async function requestAiAnswer(message) {
  const response = await fetch(CHAT_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ message })
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Serverdan noto\'g\'ri formatdagi javob keldi.');
  }

  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'API so\'rovida xatolik yuz berdi.';
    throw new Error(detail);
  }

  if (!data.answer) {
    throw new Error('API javobida answer maydoni topilmadi.');
  }

  return formatAnswerWithRagInfo(data);
}

function formatAnswerWithRagInfo(data) {
  const ragAnswerSources = [
    'vector_db',
    'exact_dataset',
    'exact_malumot_txt',
    'keyword_malumot_txt'
  ];

  if (ragAnswerSources.includes(data.answer_source)) {
    const ragInfo = data.rag_info || {};
    const source = ragInfo.source || '';
    return appendHiddenSourceLink(data.answer, source);
  }

  return data.answer;
}

function appendHiddenSourceLink(answer, source) {
  const cleanAnswer = answer
    .replace(/\[Manba\]\(https?:\/\/[^\s)]+\)/g, '')
    .replace(/\*\*Manba:\*\*\s*https?:\/\/\S+/g, '')
    .replace(/Manba:\s*https?:\/\/\S+/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  if (!source || !/^https?:\/\//.test(source)) {
    return cleanAnswer;
  }

  return `${cleanAnswer}\n\n[[SOURCE_LINK:${source}]]`;
}

function getDemoReply(input) {
  const inp = input.toLowerCase();
  if (
    inp === 'salom' ||
    inp === 'assalomu alaykum' ||
    inp === 'assalom alaykum' ||
    inp.includes('hello') ||
    inp.includes('hi')
  ) {
    return `Salom! Men Huquq AI yuridik chatbotiman.\n\nSiz menga mehnat huquqi, YTH, ijara, hujjatlar, shikoyat yoki ariza yozish bo'yicha savol berishingiz mumkin.\n\nMasalan:\n- Ish beruvchi maoshimni bermayapti\n- Passportimni ushlab qolishdi, bu qonuniymi?\n- Ijara shartnomasiz yashash muammo bo'ladimi?`;
  }
  if (
    inp.includes('sen kimsan') ||
    inp.includes('nima qilasan') ||
    inp.includes('chatbot') ||
    inp.includes('huquq ai nima') ||
    inp.includes('o\'zing haqingda') ||
    inp.includes('haqingda')
  ) {
    return `Men Huquq AI platformasining yuridik yordamchi chatbotiman.\n\nMen quyidagilarda yordam bera olaman:\n- huquqiy vaziyatni sodda tilda tushuntirish\n- tegishli qonun moddalarini topishga yo'l ko'rsatish\n- qayerga murojaat qilish kerakligini aytish\n- ariza va shikoyat matni tayyorlashga yordam berish\n\n**Eslatma:**\nMen umumiy huquqiy ma'lumot beraman. Murakkab holatlarda professional advokat bilan maslahatlashish tavsiya etiladi.`;
  }
  if (inp.includes('maosh') || inp.includes('ish') || inp.includes('employer') || inp.includes('salary')) {
    return `Agar ish beruvchi maoshingizni bermayotgan bo'lsa, vaziyatni quyidagicha baholash mumkin:\n\n**Nima qilish kerak:**\n- Ish haqi bo'yicha qarzdorlikni tasdiqlovchi hujjatlarni yig'ing\n- Ish beruvchiga yozma talabnoma yuboring\n- Natija bo'lmasa Mehnat inspeksiyasi yoki sudga murojaat qiling\n\n**Amaliy qadamlar:**\n1. Mehnat shartnomasi, buyruq, hisob-kitob varaqasi va yozishmalarni saqlang\n2. Qarzdorlik summasi va davrini aniq yozib chiqing\n3. Rasmiy ariza bilan ish beruvchidan to'lovni talab qiling\n\n**Eslatma:**\nBu umumiy huquqiy ma'lumot. Murakkab holatlarda professional advokat bilan maslahatlashish tavsiya etiladi.`;
  }
  if (inp.includes('yth') || inp.includes('avto') || inp.includes('accident') || inp.includes('yo\'l')) {
    return `Agar YTHda ayb sizda bo'lmasa, birinchi navbatda dalillarni to'g'ri rasmiylashtirish muhim:\n\n**Asosiy tavsiyalar:**\n- Hodisa joyining foto va videolarini saqlang\n- Guvohlar bo'lsa, ularning ma'lumotlarini oling\n- Bayonnoma va sug'urta hujjatlarini diqqat bilan tekshiring\n\n**Keyingi qadamlar:**\n1. YPX yoki vakolatli organ rasmiy hujjatlarini oling\n2. Sug'urta kompaniyasiga o'z vaqtida murojaat qiling\n3. Zarurat bo'lsa, yetkazilgan zararni undirish uchun da'vo tayyorlang\n\n**Eslatma:**\nHuquq AI noqonuniy yo'l tavsiya qilmaydi va sud natijasiga kafolat bermaydi.`;
  }
  if (inp.includes('ariza') || inp.includes('shikoyat') || inp.includes('draft') || inp.includes('write')) {
    return `Quyidagicha rasmiy murojaat namunasi tayyorlash mumkin:\n\n---\n\n**Kimga:** [tashkilot nomi]\n\n**Ariza**\n\nMen, [F.I.Sh], quyidagi holat bo'yicha murojaat qilaman: [vaziyatni qisqacha yozing]. Ushbu holat natijasida mening huquqlarim buzilgan deb hisoblayman.\n\nShu sababli sizdan:\n1. Holatni qonuniy tartibda ko'rib chiqishni\n2. Huquqbuzarlikni bartaraf etishni\n3. Menga yozma javob berishni so'rayman\n\nIlova: [hujjatlar ro'yxati]\n\nSana: [sana]\nImzo: __________\n\n---\n\nIstasangiz, shu namunani aynan sizning holatingizga moslab to'liq yozib beraman.`;
  }
  return `Savolingiz bo'yicha umumiy huquqiy yo'l-yo'riq quyidagicha:\n\n**Nimani aniqlash kerak:**\n- Holat qachon va qayerda yuz bergan\n- Qaysi hujjatlar yoki dalillar mavjud\n- Qarshi tomon kim va sizdan nima talab qilingan\n\n**Tavsiya etiladigan yondashuv:**\n1. Vaziyatni faktlar asosida yozib chiqing\n2. Tegishli hujjatlarni bir joyga to'plang\n3. Kerak bo'lsa, vakolatli organga yozma murojaat qiling\n\n**Muhim eslatma:**\nBu umumiy huquqiy ma'lumot hisoblanadi. Murakkab holatlarda professional advokat bilan maslahatlashish tavsiya etiladi.`;
}

// â”€â”€â”€ ADD MESSAGE â”€â”€â”€
function addMessage(role, content, options = {}) {
  const container = document.getElementById('messagesContainer');
  const div = document.createElement('div');
  div.className = `message ${role}`;
  const isUser = role === 'user';
  const persist = options.persist !== false;
  const time = options.time || getTime();

  div.innerHTML = `
    <div class="msg-avatar ${isUser ? 'user-av' : 'ai'}">${isUser ? 'AJ' : '<i class="bi bi-patch-check-fill"></i>'}</div>
    <div class="msg-content">
      <div class="msg-bubble">${isUser ? escapeHtml(content) : renderMarkdown(content)}</div>
      <div class="msg-meta">
        ${isUser ? '' : '<button class="msg-action-btn" title="Copy" onclick="copyMsg(this)"><i class="bi bi-copy"></i></button><button class="msg-action-btn" title="Regenerate"><i class="bi bi-arrow-clockwise"></i></button>'}
        <span>${time}</span>
      </div>
    </div>
  `;

  // init code copy buttons
  div.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      const code = this.closest('.code-block').querySelector('code').textContent;
      navigator.clipboard.writeText(code).catch(() => {});
      this.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
      this.classList.add('copied');
      setTimeout(() => {
        this.innerHTML = '<i class="bi bi-copy"></i> Copy';
        this.classList.remove('copied');
      }, 2000);
    });
  });

  container.appendChild(div);
  if (persist) {
    chatMessages.push({ role, content, time });
    saveStoredChatMessages();
  }
  return div;
}

function copyMsg(btn) {
  const text = btn.closest('.msg-content').querySelector('.msg-bubble').innerText;
  navigator.clipboard.writeText(text).catch(() => {});
}

// â”€â”€â”€ STREAMING â”€â”€â”€
function addStreamingMessage(content, options = {}) {
  const container = document.getElementById('messagesContainer');
  const time = getTime();
  const div = document.createElement('div');
  div.className = 'message ai';
  div.innerHTML = `
    <div class="msg-avatar ai"><i class="bi bi-patch-check-fill"></i></div>
    <div class="msg-content">
      <div class="msg-bubble" id="streamBubble"></div>
      <div class="msg-meta"><span>${time}</span></div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();

  const bubble = div.querySelector('#streamBubble');
  bubble.removeAttribute('id');
  let i = 0;
  const fullRendered = renderMarkdown(content);
  const plainText = content.replace(/\[\[SOURCE_LINK:https?:\/\/[^\]]+\]\]/g, 'Manba');
  const chars = plainText.split('');
  
  const stream = setInterval(() => {
    i += Math.floor(Math.random() * 3) + 2;
    if (i >= chars.length) {
      i = chars.length;
      clearInterval(stream);
      bubble.innerHTML = fullRendered;
      chatMessages.push({ role: 'ai', content, time });
      saveStoredChatMessages();
      if (options.speak) {
        speakAnswer(content);
      }
      // re-bind copy buttons
      bubble.querySelectorAll('.copy-btn').forEach(btn => {
        btn.addEventListener('click', function() {
          const code = this.closest('.code-block').querySelector('code').textContent;
          navigator.clipboard.writeText(code).catch(() => {});
          this.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
          this.classList.add('copied');
          setTimeout(() => {
            this.innerHTML = '<i class="bi bi-copy"></i> Copy';
            this.classList.remove('copied');
          }, 2000);
        });
      });
    } else {
      bubble.textContent = chars.slice(0, i).join('');
    }
    scrollToBottom();
  }, 18);
}

// â”€â”€â”€ TYPING INDICATOR â”€â”€â”€
function addTypingIndicator() {
  const container = document.getElementById('messagesContainer');
  const id = 'typing-' + Date.now();
  const div = document.createElement('div');
  div.className = 'message ai';
  div.id = id;
  div.innerHTML = `
    <div class="msg-avatar ai"><i class="bi bi-patch-check-fill"></i></div>
    <div class="msg-content">
      <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
      </div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
  return id;
}

function removeTypingIndicator(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// â”€â”€â”€ HELPERS â”€â”€â”€
function scrollToBottom() {
  const area = document.getElementById('chatArea');
  setTimeout(() => { area.scrollTop = area.scrollHeight; }, 10);
}

function getTime() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(text) {
  let html = escapeHtml(text);

  // Code blocks
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const l = lang || 'code';
    return `<div class="code-block"><div class="code-header"><span class="code-lang">${l}</span><button class="copy-btn"><i class="bi bi-copy"></i> Copy</button></div><pre><code>${code.trimEnd()}</code></pre></div>`;
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // Source links: show only the word "Manba", never the raw URL.
  html = html.replace(/\[\[SOURCE_LINK:(https?:\/\/[^\]]+)\]\]/g, '<a href="$1" target="_blank" rel="noopener noreferrer">Manba</a>');

  // Regular markdown links.
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // Italic
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // HR
  html = html.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:12px 0">');

  // Lists
  html = html.replace(/^(\d+)\. (.+)$/gm, '<li style="list-style-type:decimal">$2</li>');
  html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li[^>]*>.*<\/li>\n?)+/g, m => {
    const isOrdered = m.includes('decimal');
    return isOrdered ? `<ol>${m}</ol>` : `<ul>${m}</ul>`;
  });

  // Paragraphs
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  if (!html.startsWith('<')) html = '<p>' + html + '</p>';

  return html;
}

window.addEventListener('load', () => {
  currentPanel = null;
  isTyping = false;
  shouldSpeakNextAnswer = false;
  voiceModeActive = false;
  closePanel();
  closeSettings();
  showVoiceStatus(false);
  setVoiceListeningState(false);
  restoreChatOnLoad();
  updateModelUI();
  if ('speechSynthesis' in window) {
    window.speechSynthesis.getVoices();
  }
});

document.addEventListener('click', (event) => {
  const dropdown = document.getElementById('modelDropdown');
  if (dropdown && !dropdown.contains(event.target)) {
    dropdown.classList.remove('open');
  }
});
