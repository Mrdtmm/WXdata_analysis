const rootSelect = document.getElementById('root-select');
const rootInput = document.getElementById('root-input');
const rootDisplay = document.getElementById('root-display');
const folderPick = document.getElementById('folder-pick');
const scanButton = document.getElementById('scan-button');
const scanStatus = document.getElementById('scan-status');
const scanErrors = document.getElementById('scan-errors');
const accountTable = document.getElementById('account-table');
const accountBody = document.getElementById('account-body');
const accountEmpty = document.getElementById('account-empty');
const keyLamp = document.getElementById('key-lamp');
const keyStatusText = document.getElementById('key-status-text');
const keyInput = document.getElementById('key-input');
const keyValidate = document.getElementById('key-validate');
const keyResult = document.getElementById('key-result');
const keyDetail = document.getElementById('key-detail');
const keyToggle = document.getElementById('key-toggle');
const savedKeysPanel = document.getElementById('saved-keys-panel');
const savedContactStatus = document.getElementById('saved-contact-status');
const savedMessageStatus = document.getElementById('saved-message-status');
const savedMediaStatus = document.getElementById('saved-media-status');
const savedKeysHint = document.getElementById('saved-keys-hint');
const keyRevalidate = document.getElementById('key-revalidate');
const keyReextractToggle = document.getElementById('key-reextract-toggle');
const keyInputSection = document.getElementById('key-input-section');
const memoryOpen = document.getElementById('memory-open');
const messageKeysOpen = document.getElementById('message-keys-open');
const mediaKeysOpen = document.getElementById('media-keys-open');
const mediaScopeConfirm = document.getElementById('media-scope-confirm');
const mediaScopeNotice = document.getElementById('media-scope-notice');
const mediaEntryHint = document.getElementById('media-entry-hint');
const memoryAccount = document.getElementById('memory-account');
const memoryDialog = document.getElementById('memory-dialog');
const offlineConfirm = document.getElementById('offline-confirm');
const riskConfirm = document.getElementById('risk-confirm');
const memoryScan = document.getElementById('memory-scan');
const memoryStatus = document.getElementById('memory-status');
const processTable = document.getElementById('process-table');
const processBody = document.getElementById('process-body');
const memoryClose = document.getElementById('memory-close');
const contactBuild = document.getElementById('contact-build');
const contactStatus = document.getElementById('contact-status');
const contactControls = document.getElementById('contact-controls');
const contactSearch = document.getElementById('contact-search');
const contactSort = document.getElementById('contact-sort');
const contactIndexMeta = document.getElementById('contact-index-meta');
const contactTable = document.getElementById('contact-table');
const contactBody = document.getElementById('contact-body');
const contactEmpty = document.getElementById('contact-empty');
const contactSelection = document.getElementById('contact-selection');
const customDate = document.getElementById('custom-date');
const startDate = document.getElementById('start-date');
const endDate = document.getElementById('end-date');
const exportStart = document.getElementById('export-start');
const exportCancel = document.getElementById('export-cancel');
const exportOpen = document.getElementById('export-open');
const exportProgress = document.getElementById('export-progress');
const exportStatus = document.getElementById('export-status');
const exportOutput = document.getElementById('export-output');
const formatCheckboxes = Array.from(document.querySelectorAll('.export-format'));
const dateModeInputs = Array.from(document.querySelectorAll('input[name="date-mode"]'));
const exportImages = document.getElementById('export-images');
const exportFiles = document.getElementById('export-files');
const exportVideos = document.getElementById('export-videos');
const exportVoices = document.getElementById('export-voices');
const exportEmojis = document.getElementById('export-emojis');
const imageOptions = document.getElementById('image-options');
const imageQuality = document.getElementById('image-quality');
const folderDialog = document.getElementById('folder-dialog');
const folderBreadcrumb = document.getElementById('folder-breadcrumb');
const folderDrives = document.getElementById('folder-drives');
const folderWxidBanner = document.getElementById('folder-wxid-banner');
const folderList = document.getElementById('folder-list');
const folderStatus = document.getElementById('folder-status');
const folderCurrent = document.getElementById('folder-current');
const folderSelect = document.getElementById('folder-select');
const folderCancel = document.getElementById('folder-cancel');

let selectedAccount = null;
let memoryBusy = false;
let memoryScope = 'contact';
let conversations = [];
let selectedConversations = new Set();
let exportJobId = null;
let exportRunning = false;
let exportPollTimer = null;
let keyInputExpanded = false;

function setKeyLamp(state, text) {
  keyLamp.className = `lamp lamp-${state}`;
  keyStatusText.textContent = text;
}

async function fetchKeyStatus() {
  const params = new URLSearchParams({
    root: rootInput.value.trim() || rootSelect.value,
    account_dir: selectedAccount,
  });
  const response = await fetch(`/api/key/status?${params}`);
  return response.json();
}

function renderKeyPanel(data) {
  const dbKeys = data.db_keys || { total: 0, contact: false, message_count: 0, media_count: 0 };
  const hasContact = data.configured;
  const hasAnyKeys = hasContact || dbKeys.total > 0;

  if (hasContact) {
    setKeyLamp('on', '密钥已保存，可直接进入第 3 步');
  } else if (dbKeys.total > 0) {
    setKeyLamp('off', '已有部分密钥，还需联系人密钥');
  } else {
    setKeyLamp('off', '这个账号还没有密钥');
  }

  if (hasAnyKeys) {
    savedKeysPanel.classList.remove('hidden');
    savedContactStatus.textContent = hasContact ? '✓ 已保存并校验' : '未保存';
    savedContactStatus.className = `saved-key-status ${hasContact ? 'ok' : 'missing'}`;
    savedMessageStatus.textContent = dbKeys.message_count > 0 ? `✓ ${dbKeys.message_count} 个` : '未保存';
    savedMessageStatus.className = `saved-key-status ${dbKeys.message_count > 0 ? 'ok' : 'missing'}`;
    savedMediaStatus.textContent = dbKeys.media_count > 0 ? `✓ ${dbKeys.media_count} 个` : '未保存';
    savedMediaStatus.className = `saved-key-status ${dbKeys.media_count > 0 ? 'ok' : 'missing'}`;
    if (hasContact && dbKeys.message_count > 0) {
      savedKeysHint.textContent = '密钥齐全，可直接进入第 3 步生成联系人列表。密钥不随登录变化，无需重复提取。';
    } else if (hasContact) {
      savedKeysHint.textContent = '联系人密钥已就绪。如需导出消息，还需准备消息库密钥。';
    } else {
      savedKeysHint.textContent = '已有部分分库密钥，但还缺联系人密钥。请提取或手动输入。';
    }
  } else {
    savedKeysPanel.classList.add('hidden');
  }

  if (hasContact) {
    keyInputExpanded = false;
    keyInputSection.classList.add('hidden');
    keyReextractToggle.textContent = '需要重新提取密钥？';
    keyReextractToggle.classList.remove('hidden');
  } else {
    keyInputSection.classList.remove('hidden');
    keyReextractToggle.classList.add('hidden');
  }
}

async function refreshKeyStatus() {
  keyResult.textContent = '';
  keyResult.classList.remove('error');
  keyDetail.textContent = '';
  if (!selectedAccount) {
    setKeyLamp('off', '未配置密钥（先在上面选一个账号）');
    savedKeysPanel.classList.add('hidden');
    keyInputSection.classList.remove('hidden');
    keyReextractToggle.classList.add('hidden');
    return;
  }
  try {
    const data = await fetchKeyStatus();
    renderKeyPanel(data);
    if (data.configured && data.params) {
      keyDetail.textContent = `实测参数：${JSON.stringify(data.params)}`;
    }
  } catch (error) {
    setKeyLamp('bad', `读取密钥状态失败：${error.message}`);
  }
}

keyReextractToggle.addEventListener('click', () => {
  keyInputExpanded = !keyInputExpanded;
  keyInputSection.classList.toggle('hidden', !keyInputExpanded);
  keyReextractToggle.textContent = keyInputExpanded ? '收起密钥输入与提取' : '需要重新提取密钥？';
});

keyRevalidate.addEventListener('click', async () => {
  if (!selectedAccount) return;
  keyRevalidate.disabled = true;
  keyResult.classList.remove('error');
  keyResult.textContent = '正在用已保存密钥重新校验 contact.db，可能要十几秒…';
  keyDetail.textContent = '';
  try {
    const response = await fetch('/api/key/revalidate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: rootInput.value.trim() || rootSelect.value,
        account_dir: selectedAccount,
      }),
    });
    const data = await response.json();
    if (data.ok) {
      setKeyLamp('on', '已保存密钥校验通过，对应此账号');
      keyResult.textContent = `校验通过：${data.pages} 页，${data.hmac_note}`;
      keyDetail.textContent =
        `实测参数：${JSON.stringify(data.params)}\n` +
        `读到的表（只看结构，不读聊天内容）：${data.tables.join('、') || '没有表'}\n` +
        (data.wal_present ? data.wal_note : '');
    } else {
      setKeyLamp('bad', '已保存密钥校验未通过');
      keyResult.textContent = data.reason || '未知原因';
      keyResult.classList.add('error');
    }
  } catch (error) {
    keyResult.textContent = `请求失败：${error.message}`;
    keyResult.classList.add('error');
  } finally {
    keyRevalidate.disabled = false;
  }
});

accountBody.addEventListener('change', (event) => {
  if (event.target.name === 'account') {
    selectedAccount = event.target.value;
    if (memoryDialog.open) memoryDialog.close();
    contactBuild.disabled = false;
    resetContacts();
    refreshKeyStatus();
    loadContactIndex();
  }
});

keyToggle.addEventListener('click', () => {
  const show = keyInput.type === 'password';
  keyInput.type = show ? 'text' : 'password';
  keyToggle.setAttribute('aria-label', show ? '隐藏密钥' : '显示密钥');
  keyToggle.title = show ? '隐藏密钥' : '显示密钥';
});

keyValidate.addEventListener('click', async () => {
  if (!selectedAccount) {
    keyResult.textContent = '请先在第 1 步的账号列表里选中一个账号。';
    keyResult.classList.add('error');
    return;
  }
  keyResult.classList.remove('error');
  keyDetail.textContent = '';
  keyValidate.disabled = true;
  keyResult.textContent = '正在复制数据库副本并试解密，可能要十几秒…';
  try {
    const response = await fetch('/api/key/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: rootInput.value.trim() || rootSelect.value,
        account_dir: selectedAccount,
        key_hex: keyInput.value,
      }),
    });
    const data = await response.json();
    if (data.ok) {
      setKeyLamp('on', '密钥校验通过');
      keyResult.textContent = `解密成功：${data.pages} 页，${data.hmac_note}`;
      keyDetail.textContent =
        `实测参数：${JSON.stringify(data.params)}\n` +
        `读到的表（只看结构，不读聊天内容）：${data.tables.join('、') || '没有表'}\n` +
        (data.wal_present ? data.wal_note : '');
      try { renderKeyPanel(await fetchKeyStatus()); } catch (_) {}
    } else {
      setKeyLamp('bad', '密钥校验未通过');
      keyResult.textContent = data.reason || '未知原因';
      keyResult.classList.add('error');
    }
  } catch (error) {
    keyResult.textContent = `请求失败：${error.message}`;
    keyResult.classList.add('error');
  } finally {
    keyValidate.disabled = false;
  }
});

function setMemoryStatus(text, isError = false) {
  memoryStatus.textContent = text;
  memoryStatus.classList.toggle('error', isError);
}

function updateMemoryControls() {
  const confirmed = Boolean(selectedAccount) && offlineConfirm.checked && riskConfirm.checked
    && (memoryScope !== 'media' || mediaScopeConfirm.checked);
  memoryScan.disabled = memoryBusy || !confirmed;
  memoryClose.disabled = memoryBusy;
}

function resetMemoryDialog() {
  offlineConfirm.checked = false;
  riskConfirm.checked = false;
  mediaScopeConfirm.checked = false;
  memoryBusy = false;
  processBody.innerHTML = '';
  processTable.classList.add('hidden');
  setMemoryStatus('');
  updateMemoryControls();
}

function renderInstances(items) {
  processBody.innerHTML = '';
  processTable.classList.toggle('hidden', items.length === 0);
  for (const item of items) {
    const row = document.createElement('tr');
    row.innerHTML = '<td class="mono"></td><td></td><td></td><td class="mono"></td><td class="hint">无法确认</td>';
    row.children[0].textContent = item.root_pid;
    row.children[1].textContent = item.process_count;
    row.children[2].textContent = (item.windows || []).join('；') || '没有窗口标题';
    row.children[3].textContent = item.started_at || '读取不到';
    processBody.appendChild(row);
  }
  updateMemoryControls();
}

function openMemoryDialog(scope) {
  memoryScope = scope;
  keyResult.classList.remove('error');
  resetMemoryDialog();
  memoryAccount.textContent = selectedAccount
    ? `当前所选账号目录：${selectedAccount}`
    : '尚未选择账号。请关闭此窗口，回到第 1 步扫描并选中一个账号，再打开此窗口。';
  memoryAccount.classList.toggle('error', !selectedAccount);
  mediaScopeNotice.classList.toggle('hidden', scope !== 'media');
  const title = document.getElementById('memory-title');
  title.textContent = {
    contact: '从微信内存获取密钥',
    messages: '为联系人列表准备消息库密钥',
    media: '准备媒体库密钥（语音可解码 WAV / 表情排查）',
  }[scope];
  memoryDialog.showModal();
  title.focus();
  memoryDialog.scrollTop = 0;
}

memoryOpen.addEventListener('click', () => openMemoryDialog('contact'));
messageKeysOpen.addEventListener('click', () => openMemoryDialog('messages'));
mediaKeysOpen.addEventListener('click', () => openMemoryDialog('media'));

for (const checkbox of [offlineConfirm, riskConfirm, mediaScopeConfirm]) {
  checkbox.addEventListener('change', updateMemoryControls);
}

memoryClose.addEventListener('click', () => memoryDialog.close());
memoryDialog.addEventListener('cancel', (event) => {
  if (memoryBusy) event.preventDefault();
});

async function extractMemoryKey() {
  setMemoryStatus('检测到 1 个微信登录实例，正在只读提取并验证密钥，可能需要几分钟…');
  try {
    const response = await fetch('/api/memory/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: rootInput.value.trim() || rootSelect.value,
        account_dir: selectedAccount,
        offline_confirmed: offlineConfirm.checked,
        risk_confirmed: riskConfirm.checked,
        scope: memoryScope,
        media_confirmed: memoryScope === 'media' && mediaScopeConfirm.checked,
      }),
    });
    const data = await response.json();
    if (!data.ok) {
      setMemoryStatus(data.reason || '密钥提取失败。', true);
      return;
    }
    if (memoryScope === 'media') {
      keyResult.classList.remove('error');
      keyResult.textContent = `已验证并保存 ${data.media_key_count} 个媒体库密钥。请完全退出微信后再进行离线导出，语音将解码为 WAV 播放；表情尚未完成还原。`;
      keyDetail.textContent = data.databases.map((item) =>
        `${item.database}：${item.pages} 页，${item.hmac_note}`
      ).join('\n');
      memoryDialog.close();
      try { renderKeyPanel(await fetchKeyStatus()); } catch (_) {}
      return;
    }
    keyInput.value = data.key_hex;
    setKeyLamp('on', '已自动取得密钥，并通过真实数据库校验');
    keyResult.classList.remove('error');
    keyResult.textContent = `解密成功：${data.pages} 页，${data.hmac_note}`;
    keyDetail.textContent =
      `实测参数：${JSON.stringify(data.params)}\n` +
      `读到的表（只看结构，不读聊天内容）：${data.tables.join('、') || '没有表'}\n` +
      (data.message_key_count !== undefined ? `消息库密钥：${data.message_key_count} 个\n` : '') +
      (data.wal_present ? data.wal_note : '');
    memoryDialog.close();
    try { renderKeyPanel(await fetchKeyStatus()); } catch (_) {}
  } catch (error) {
    setMemoryStatus(`提取请求失败：${error.message}`, true);
  }
}

memoryScan.addEventListener('click', async () => {
  memoryBusy = true;
  processBody.innerHTML = '';
  processTable.classList.add('hidden');
  setMemoryStatus('正在读取微信进程列表…');
  updateMemoryControls();
  try {
    const response = await fetch('/api/memory/processes');
    const data = await response.json();
    if (!data.ok) {
      setMemoryStatus(data.reason || '读取进程列表失败。', true);
      return;
    }
    const instances = data.instances || [];
    renderInstances(instances);
    if (instances.length === 0) {
      setMemoryStatus('没有找到正在运行的 Weixin.exe。请先启动微信并登录所选账号。', true);
    } else if (instances.length > 1) {
      setMemoryStatus(`检测到 ${instances.length} 个微信登录实例。多微信读取冲突，已停止；请只保留所选账号登录后重试。`, true);
    } else {
      await extractMemoryKey();
    }
  } catch (error) {
    setMemoryStatus(`读取进程列表失败：${error.message}`, true);
  } finally {
    memoryBusy = false;
    updateMemoryControls();
  }
});

function setContactStatus(text, isError = false) {
  contactStatus.textContent = text;
  contactStatus.classList.toggle('error', isError);
}

function resetContacts() {
  conversations = [];
  selectedConversations.clear();
  contactSearch.value = '';
  contactBody.innerHTML = '';
  contactControls.classList.add('hidden');
  contactTable.classList.add('hidden');
  contactEmpty.classList.add('hidden');
  contactIndexMeta.textContent = '';
  updateContactSelection();
  resetExportUI();
  setContactStatus(selectedAccount ? '这个账号还没有加载联系人列表。' : '请先扫描并选择账号。');
}

function formatContactTime(timestamp) {
  if (!timestamp) return '无记录';
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false });
}

function applyContactIndex(index) {
  conversations = index.conversations || [];
  const available = new Set(conversations.map((item) => item.username));
  selectedConversations = new Set(Array.from(selectedConversations).filter((username) => available.has(username)));
  updateContactSelection();
  contactControls.classList.remove('hidden');
  setContactStatus(`已列出 ${conversations.length} 个有消息的会话，共 ${index.total_messages || 0} 条消息。`);
  const generated = index.generated_at ? new Date(index.generated_at).toLocaleString('zh-CN', { hour12: false }) : '未知';
  const warnings = (index.warnings || []).length ? `；${index.warnings.length} 个表未计入` : '';
  contactIndexMeta.textContent = `索引生成于 ${generated}${warnings}。只统计主库，不合并 WAL。`;
  renderContacts();
}

function renderContacts() {
  const query = contactSearch.value.trim().toLocaleLowerCase();
  const filtered = conversations.filter((item) => {
    const haystack = `${item.nickname || ''} ${item.remark || ''} ${item.username || ''}`.toLocaleLowerCase();
    return haystack.includes(query);
  });
  const sort = contactSort.value;
  filtered.sort((a, b) => {
    if (sort === 'first-asc') return a.first_timestamp - b.first_timestamp;
    if (sort === 'count-desc') return b.message_count - a.message_count;
    if (sort === 'count-asc') return a.message_count - b.message_count;
    if (sort === 'name-asc') {
      const aName = a.remark || a.nickname || a.username;
      const bName = b.remark || b.nickname || b.username;
      return aName.localeCompare(bName, 'zh-CN');
    }
    return b.last_timestamp - a.last_timestamp;
  });

  contactBody.innerHTML = '';
  for (const item of filtered) {
    const row = document.createElement('tr');
    row.innerHTML = '<td><input type="checkbox" aria-label="选择会话"></td><td></td><td></td><td class="mono"></td><td></td><td class="numeric"></td><td class="mono"></td>';
    const checkbox = row.children[0].firstElementChild;
    checkbox.checked = selectedConversations.has(item.username);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        selectedConversations.add(item.username);
      } else {
        selectedConversations.delete(item.username);
      }
      updateContactSelection();
    });
    row.children[1].textContent = item.nickname || '未读取到昵称';
    row.children[2].textContent = item.remark || '—';
    row.children[3].textContent = item.username;
    row.children[4].textContent = item.session_type;
    row.children[5].textContent = item.message_count;
    row.children[6].textContent = `${formatContactTime(item.first_timestamp)} 至 ${formatContactTime(item.last_timestamp)}`;
    contactBody.appendChild(row);
  }
  contactTable.classList.toggle('hidden', filtered.length === 0);
  contactEmpty.classList.toggle('hidden', filtered.length > 0);
  if (!filtered.length) {
    contactEmpty.textContent = conversations.length ? '没有符合搜索条件的会话。' : '这个索引里没有有消息的会话。';
  }
}

async function loadContactIndex() {
  if (!selectedAccount) return;
  const account = selectedAccount;
  try {
    const params = new URLSearchParams({ account_dir: account });
    const response = await fetch(`/api/contacts?${params}`);
    const data = await response.json();
    if (account !== selectedAccount) return;
    if (!data.ok) {
      setContactStatus(data.reason || '读取联系人索引失败。', true);
      return;
    }
    if (data.configured && data.index) {
      applyContactIndex(data.index);
    } else {
      setContactStatus('这个账号还没有联系人索引。准备好分库密钥并退出微信后，点「生成联系人列表」。');
    }
  } catch (error) {
    if (account === selectedAccount) setContactStatus(`读取联系人索引失败：${error.message}`, true);
  }
}

contactBuild.addEventListener('click', async () => {
  if (!selectedAccount) {
    setContactStatus('请先在第 1 步选择账号。', true);
    return;
  }
  const account = selectedAccount;
  contactBuild.disabled = true;
  setContactStatus('正在复制数据库副本、解密并统计消息，请先确保微信已完全退出…');
  try {
    const response = await fetch('/api/contacts/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: rootInput.value.trim() || rootSelect.value,
        account_dir: account,
      }),
    });
    const data = await response.json();
    if (account !== selectedAccount) return;
    if (!data.ok) {
      setContactStatus(data.reason || '生成联系人列表失败。', true);
      return;
    }
    applyContactIndex(data.index);
  } catch (error) {
    if (account === selectedAccount) setContactStatus(`生成联系人列表失败：${error.message}`, true);
  } finally {
    contactBuild.disabled = !selectedAccount;
  }
});

contactSearch.addEventListener('input', renderContacts);
contactSort.addEventListener('change', renderContacts);

function getSelectedFormats() {
  return formatCheckboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
}

function getDateMode() {
  return dateModeInputs.find((input) => input.checked)?.value || 'all';
}

function hasValidDateRange() {
  if (getDateMode() === 'all') return true;
  return Boolean(startDate.value && endDate.value && startDate.value <= endDate.value);
}

function updateContactSelection() {
  const count = selectedConversations.size;
  contactSelection.textContent = count ? `已选择 ${count} 个会话。` : '还没有选择要导出的会话。';
  updateExportControls();
}

function updateExportControls() {
  const hasSelection = selectedConversations.size > 0;
  const hasFormat = getSelectedFormats().length > 0;
  const validDates = hasValidDateRange();
  exportStart.disabled = exportRunning || !selectedAccount || !hasSelection || !hasFormat || !validDates;
  if (exportRunning) return;
  if (!selectedAccount || !hasSelection) {
    exportStatus.textContent = '请先在第 3 步勾选至少一个会话。';
  } else if (!hasFormat) {
    exportStatus.textContent = '请至少勾选一种导出格式。';
  } else if (!validDates) {
    exportStatus.textContent = '请填写完整日期，并确保开始日期不晚于结束日期。';
  } else if (!exportJobId) {
    exportStatus.textContent = `准备导出 ${selectedConversations.size} 个会话。`;
  }
}

function setAccountControlsDisabled(disabled) {
  scanButton.disabled = disabled;
  folderPick.disabled = disabled;
  rootSelect.disabled = disabled;
  for (const radio of document.querySelectorAll('input[name="account"]')) radio.disabled = disabled;
  contactBuild.disabled = disabled || !selectedAccount;
  keyValidate.disabled = disabled;
  keyRevalidate.disabled = disabled;
  memoryOpen.disabled = disabled;
  messageKeysOpen.disabled = disabled;
  mediaKeysOpen.disabled = disabled;
}

function resetExportUI() {
  if (exportPollTimer) clearTimeout(exportPollTimer);
  exportPollTimer = null;
  exportJobId = null;
  exportRunning = false;
  exportProgress.classList.add('hidden');
  exportProgress.value = 0;
  exportCancel.classList.add('hidden');
  exportOpen.classList.add('hidden');
  exportOutput.textContent = '';
  exportStatus.classList.remove('error');
  setAccountControlsDisabled(false);
  updateExportControls();
}

function scheduleExportPoll() {
  if (exportPollTimer) clearTimeout(exportPollTimer);
  exportPollTimer = setTimeout(pollExport, 300);
}

function updateExportJob(job) {
  exportProgress.value = job.percent || 0;
  exportOutput.textContent = job.output_dir || '';
  if (job.state === 'running') {
    exportStatus.textContent = `${job.stage}：${job.processed || 0} / ${job.total || 0}`;
    return;
  }
  exportRunning = false;
  exportCancel.classList.add('hidden');
  setAccountControlsDisabled(false);
  updateExportControls();
  if (job.state === 'completed') {
    exportStatus.classList.remove('error');
    const mediaText = job.media
      ? `；图片文件 ${job.media.unique_files} 个，缺失 ${job.media.missing} 张，失败 ${job.media.failed} 张`
      : '';
    const fileText = job.file_media
      ? `；接收文件 ${job.file_media.unique_files} 个，缺失 ${job.file_media.missing} 个，失败 ${job.file_media.failed} 个`
      : '';
    const mediaLabels = { video: '视频', voice: '语音', emoji: '表情' };
    const otherText = job.other_media
      ? Object.entries(job.other_media).map(([kind, stats]) => {
        const label = mediaLabels[kind];
        return `；${label}导出 ${stats.exported} 条，缺失 ${stats.missing} 条，未验证 ${stats.unverified} 条，失败 ${stats.failed} 条`;
      }).join('')
      : '';
    exportStatus.textContent = `导出完成：${job.conversation_count} 个会话，${job.message_count} 条消息${mediaText}${fileText}${otherText}。`;
    exportOpen.classList.remove('hidden');
  } else if (job.state === 'cancelled') {
    exportStatus.classList.remove('error');
    exportStatus.textContent = '导出已取消，本次临时文件已清理。';
  } else {
    exportStatus.textContent = job.error || '导出失败。';
    exportStatus.classList.add('error');
  }
}

async function pollExport() {
  if (!exportJobId) return;
  try {
    const params = new URLSearchParams({ job_id: exportJobId });
    const response = await fetch(`/api/export/status?${params}`);
    const data = await response.json();
    if (!data.ok) throw new Error(data.reason || '读取导出状态失败');
    updateExportJob(data.job);
    if (data.job.state === 'running') scheduleExportPoll();
  } catch (error) {
    exportRunning = false;
    exportCancel.classList.add('hidden');
    setAccountControlsDisabled(false);
    updateExportControls();
    exportStatus.textContent = `读取导出状态失败：${error.message}。后台任务可能仍在运行，请刷新页面后再试。`;
    exportStatus.classList.add('error');
  }
}

exportStart.addEventListener('click', async () => {
  if (exportStart.disabled) return;
  const anyMediaSelected = exportImages.checked || exportFiles.checked
    || exportVideos.checked || exportVoices.checked || exportEmojis.checked;
  if (getDateMode() === 'all' && anyMediaSelected && !window.confirm(
    '全部日期导出媒体可能耗时较长，并占用较多磁盘空间；只会处理本次勾选的会话和媒体类型。确认继续吗？',
  )) return;
  exportRunning = true;
  exportJobId = null;
  exportStatus.classList.remove('error');
  exportOutput.textContent = '';
  exportOpen.classList.add('hidden');
  exportCancel.classList.remove('hidden');
  exportCancel.disabled = false;
  exportProgress.classList.remove('hidden');
  exportProgress.value = 0;
  exportStatus.textContent = '正在创建导出任务…';
  setAccountControlsDisabled(true);
  updateExportControls();
  try {
    const response = await fetch('/api/export/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: rootInput.value.trim() || rootSelect.value,
        account_dir: selectedAccount,
        usernames: Array.from(selectedConversations),
        date_mode: getDateMode(),
        start_date: getDateMode() === 'custom' ? startDate.value : null,
        end_date: getDateMode() === 'custom' ? endDate.value : null,
        formats: getSelectedFormats(),
        include_images: exportImages.checked,
        image_quality: imageQuality.value,
        include_files: exportFiles.checked,
        include_videos: exportVideos.checked,
        include_voices: exportVoices.checked,
        include_emojis: exportEmojis.checked,
      }),
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.reason || '创建导出任务失败');
    exportJobId = data.job.job_id;
    updateExportJob(data.job);
    if (data.job.state === 'running') scheduleExportPoll();
  } catch (error) {
    exportRunning = false;
    exportCancel.classList.add('hidden');
    setAccountControlsDisabled(false);
    updateExportControls();
    exportStatus.textContent = error.message;
    exportStatus.classList.add('error');
  }
});

exportCancel.addEventListener('click', async () => {
  if (!exportJobId) return;
  exportCancel.disabled = true;
  try {
    const response = await fetch('/api/export/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: exportJobId }),
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.reason || '取消失败');
    exportStatus.textContent = '正在取消…';
    scheduleExportPoll();
  } catch (error) {
    exportStatus.textContent = error.message;
    exportStatus.classList.add('error');
  } finally {
    exportCancel.disabled = false;
  }
});

exportOpen.addEventListener('click', async () => {
  if (!exportJobId) return;
  try {
    const response = await fetch('/api/export/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: exportJobId }),
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.reason || '打开目录失败');
  } catch (error) {
    exportStatus.textContent = error.message;
    exportStatus.classList.add('error');
  }
});

for (const input of dateModeInputs) {
  input.addEventListener('change', () => {
    customDate.classList.toggle('hidden', getDateMode() !== 'custom');
    updateExportControls();
  });
}
for (const input of [startDate, endDate]) input.addEventListener('input', updateExportControls);
for (const checkbox of formatCheckboxes) checkbox.addEventListener('change', updateExportControls);
exportImages.addEventListener('change', () => {
  imageOptions.classList.toggle('hidden', !exportImages.checked);
});

function formatBytes(value) {
  if (!value) return '0';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function setRootPath(path) {
  rootInput.value = path || '';
  if (path) {
    rootDisplay.textContent = path;
    rootDisplay.classList.remove('empty');
  } else {
    rootDisplay.textContent = '尚未选择';
    rootDisplay.classList.add('empty');
  }
}

/* ── Folder browser ── */
let folderPath = '';
let folderBusy = false;
let cachedDrives = [];

function openFolderBrowser() {
  folderPath = rootInput.value.trim() || '';
  folderDialog.showModal();
  browseTo(folderPath);
}

async function browseTo(path) {
  if (folderBusy) return;
  folderBusy = true;
  folderSelect.disabled = true;
  folderList.innerHTML = '<div class="folder-loading">正在读取目录…</div>';
  folderStatus.textContent = '';
  folderStatus.classList.remove('error');
  try {
    const params = new URLSearchParams({ path: path || '' });
    const response = await fetch(`/api/browse?${params}`);
    const data = await response.json();
    if (!data.ok) {
      folderList.innerHTML = `<div class="folder-error">${escapeHtml(data.reason || '读取失败')}</div>`;
      folderStatus.textContent = data.reason || '读取失败';
      folderStatus.classList.add('error');
      if (data.drives) renderDrives(data.drives, '');
      return;
    }
    folderPath = data.path || '';
    if (data.drives && data.drives.length) cachedDrives = data.drives;
    renderDrives(cachedDrives, folderPath);
    renderBreadcrumb(folderPath);
    renderFolderList(data.dirs, data.wxid_count);
    folderCurrent.textContent = folderPath || '（此电脑）';
    folderSelect.disabled = !folderPath;
    folderStatus.textContent = data.dirs.length
      ? `${data.dirs.length} 个子文件夹`
      : '没有子文件夹';
  } catch (error) {
    folderList.innerHTML = `<div class="folder-error">请求失败：${escapeHtml(error.message)}</div>`;
    folderStatus.textContent = `请求失败：${error.message}`;
    folderStatus.classList.add('error');
  } finally {
    folderBusy = false;
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function renderDrives(drives, currentPath) {
  folderDrives.innerHTML = '';
  const thisPC = document.createElement('button');
  thisPC.type = 'button';
  thisPC.className = 'folder-drive' + (currentPath ? '' : ' active');
  thisPC.textContent = '此电脑';
  thisPC.addEventListener('click', () => browseTo(''));
  folderDrives.appendChild(thisPC);
  for (const drive of drives) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'folder-drive';
    if (currentPath && currentPath.toUpperCase().startsWith(drive.name.toUpperCase())) {
      btn.classList.add('active');
    }
    btn.textContent = drive.name;
    btn.addEventListener('click', () => browseTo(drive.path));
    folderDrives.appendChild(btn);
  }
}

function renderBreadcrumb(path) {
  folderBreadcrumb.innerHTML = '';
  if (!path) {
    const span = document.createElement('span');
    span.className = 'folder-crumb current';
    span.textContent = '此电脑';
    folderBreadcrumb.appendChild(span);
    return;
  }
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/);
  let accumulated = '';
  parts.forEach((part, index) => {
    if (index > 0) {
      const sep = document.createElement('span');
      sep.className = 'folder-crumb-sep';
      sep.textContent = '›';
      folderBreadcrumb.appendChild(sep);
    }
    accumulated = index === 0 ? part + '\\' : accumulated + part + '\\';
    const crumb = document.createElement('button');
    crumb.type = 'button';
    crumb.className = 'folder-crumb';
    crumb.textContent = part || '此电脑';
    const target = accumulated.replace(/\\+$/, '') || accumulated;
    if (index === parts.length - 1) {
      crumb.classList.add('current');
    } else {
      crumb.addEventListener('click', () => browseTo(target));
    }
    folderBreadcrumb.appendChild(crumb);
  });
}

function renderFolderList(dirs, wxidCount) {
  folderList.innerHTML = '';
  if (wxidCount > 0) {
    folderWxidBanner.textContent = `✓ 此目录包含 ${wxidCount} 个微信账号文件夹（wxid_…），可以直接点「选择此文件夹」。`;
    folderWxidBanner.classList.remove('hidden');
  } else {
    folderWxidBanner.classList.add('hidden');
  }
  if (!dirs.length) {
    folderList.innerHTML = '<div class="folder-empty">此目录下没有子文件夹</div>';
    return;
  }
  for (const dir of dirs) {
    const entry = document.createElement('div');
    entry.className = 'folder-entry' + (dir.is_wxid ? ' is-wxid' : '');
    entry.setAttribute('role', 'option');
    entry.tabIndex = 0;
    const icon = document.createElement('span');
    icon.className = 'folder-entry-icon';
    icon.textContent = dir.is_wxid ? '👤' : '📁';
    icon.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.className = 'folder-entry-name';
    name.textContent = dir.name;
    name.title = dir.path;
    entry.appendChild(icon);
    entry.appendChild(name);
    if (dir.is_wxid) {
      const badge = document.createElement('span');
      badge.className = 'folder-entry-badge';
      badge.textContent = '微信账号';
      entry.appendChild(badge);
    }
    entry.addEventListener('click', () => browseTo(dir.path));
    entry.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        browseTo(dir.path);
      }
    });
    folderList.appendChild(entry);
  }
}

folderPick.addEventListener('click', openFolderBrowser);
folderCancel.addEventListener('click', () => folderDialog.close());
folderSelect.addEventListener('click', () => {
  if (!folderPath) return;
  setRootPath(folderPath);
  folderDialog.close();
  scan();
});
folderDialog.addEventListener('click', (event) => {
  if (event.target === folderDialog) folderDialog.close();
});

function setStatus(text, isError = false) {
  scanStatus.textContent = text;
  scanStatus.classList.toggle('error', isError);
}

async function loadConfig() {
  try {
    const response = await fetch('/api/config');
    const data = await response.json();
    rootSelect.innerHTML = '';
    if (!data.roots.length) {
      rootSelect.innerHTML = '<option value="">（还没有配置数据目录，请点上面按钮选择文件夹）</option>';
      setRootPath('');
      return;
    }
    for (const item of data.roots) {
      const option = document.createElement('option');
      option.value = item.path;
      option.textContent = item.exists ? item.path : `${item.path}（目录不存在）`;
      rootSelect.appendChild(option);
    }
    setRootPath(data.roots[0].path);
  } catch (error) {
    setStatus(`读取配置失败：${error.message}`, true);
  }
}

function renderAccounts(accounts, hasErrors) {
  accountBody.innerHTML = '';
  accountTable.classList.toggle('hidden', accounts.length === 0);
  accountEmpty.classList.toggle('hidden', accounts.length > 0);
  if (!accounts.length) {
    accountEmpty.textContent = hasErrors
      ? '没有可显示的账号，请先解决上面的报错。'
      : '这个目录下没有找到 wxid_ 开头的账号文件夹。请确认选的是 xwechat_files 那一层。';
    return;
  }
  for (const account of accounts) {
    const row = document.createElement('tr');
    const notes = [];
    if (account.is_most_recent) notes.push('最近活跃');
    if (!account.has_db_storage) notes.push('没有 db_storage');
    if (!account.has_msg) notes.push('没有 msg');
    row.innerHTML = `
      <td><input type="radio" name="account" value=""></td>
      <td class="mono"></td>
      <td class="hint">需解密后才能显示</td>
      <td>${formatBytes(account.total_bytes)}</td>
      <td class="mono">${account.db_storage_mtime || '无 db_storage'}</td>
      <td>${formatBytes(account.msg_bytes)}</td>
      <td>${notes.join('；')}</td>`;
    row.children[0].firstElementChild.value = account.dir_name;
    row.children[1].textContent = account.wxid;
    row.title = account.path;
    accountBody.appendChild(row);
  }
}

async function scan() {
  const root = rootInput.value.trim() || rootSelect.value;
  scanErrors.innerHTML = '';
  selectedAccount = null;
  contactBuild.disabled = true;
  resetContacts();
  setKeyLamp('off', '未配置密钥');
  savedKeysPanel.classList.add('hidden');
  keyInputSection.classList.remove('hidden');
  keyReextractToggle.classList.add('hidden');
  keyInputExpanded = false;
  keyResult.textContent = '';
  keyDetail.textContent = '';
  keyInput.value = '';
  scanButton.disabled = true;
  setStatus('正在扫描并统计体积，账号目录大的话可能要等几十秒，请别关页面…');
  try {
    const response = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root }),
    });
    const data = await response.json();
    for (const item of data.errors || []) {
      const line = document.createElement('p');
      line.textContent = item.root ? `${item.root}：${item.message}` : item.message;
      scanErrors.appendChild(line);
    }
    renderAccounts(data.accounts || [], (data.errors || []).length > 0);
    const count = (data.accounts || []).length;
    setStatus(count ? `扫描完成，找到 ${count} 个账号。` : '扫描完成。');
  } catch (error) {
    setStatus(`扫描失败：${error.message}`, true);
  } finally {
    scanButton.disabled = false;
  }
}

rootSelect.addEventListener('change', () => {
  setRootPath(rootSelect.value);
});
scanButton.addEventListener('click', scan);
mediaKeysOpen.disabled = false;
mediaEntryHint.textContent = '媒体入口已就绪：点击后打开确认窗口，不会立即读取内存。';
loadConfig();
