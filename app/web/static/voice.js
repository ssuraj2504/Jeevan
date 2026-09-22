(function () {
  'use strict';

  const app = window.JeevanApp;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const nodes = {
    toggle: document.querySelector('#voice-toggle'),
    stop: document.querySelector('#voice-stop'),
    indicator: document.querySelector('#voice-indicator'),
    status: document.querySelector('#voice-status'),
    conversation: document.querySelector('#voice-conversation'),
    heard: document.querySelector('#voice-heard'),
    reply: document.querySelector('#voice-reply'),
    review: document.querySelector('#voice-review'),
    transcript: document.querySelector('#voice-transcript'),
    reinterpret: document.querySelector('#voice-reinterpret'),
    start: document.querySelector('#voice-start-task')
  };
  const voice = { active: false, listening: false, busy: false, recognition: null, draft: null, ready: false, missing: [], taskId: null, generation: 0 };
  const missingLabels = {
    service_type: 'service (this demo supports AC servicing)',
    requested_date: 'date, for example 26 September 2026',
    time_window: 'time, for example after 2 PM',
    budget_rupees: 'maximum budget',
    address: 'service address'
  };

  function status(message, mode = 'idle') {
    nodes.status.textContent = message;
    nodes.indicator.dataset.mode = mode;
    nodes.toggle.textContent = voice.listening ? '● Listening…' : '🎙 Listen again';
  }

  function showTurn(heard, reply) {
    nodes.conversation.hidden = false;
    nodes.heard.textContent = heard || '—';
    nodes.reply.textContent = reply;
  }

  function pauseListening() {
    const recognition = voice.recognition;
    voice.generation += 1;
    voice.recognition = null;
    voice.listening = false;
    if (recognition) {
      try { recognition.abort(); } catch (_) { /* already stopped */ }
    }
  }

  function stopSession() {
    voice.active = false;
    pauseListening();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    voice.listening = false;
    voice.busy = false;
    nodes.stop.hidden = true;
    nodes.toggle.textContent = '🎙 Start listening';
    nodes.toggle.disabled = false;
    status('Microphone is off. You can still edit and submit the task fields.', 'idle');
    nodes.toggle.textContent = '🎙 Start listening';
  }

  function speak(message, listenAfter = false) {
    nodes.reply.textContent = message;
    if (!voice.active || !('speechSynthesis' in window) || !window.SpeechSynthesisUtterance) {
      if (listenAfter && voice.active) startListening();
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = 'en-IN';
    utterance.rate = 1;
    utterance.onend = () => { if (listenAfter && voice.active) startListening(); };
    utterance.onerror = () => { if (listenAfter && voice.active) startListening(); };
    status('Jeevan is speaking.', 'speaking');
    window.speechSynthesis.speak(utterance);
  }

  function startListening() {
    if (!Recognition || !voice.active || voice.listening || voice.busy) return;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    const recognition = new Recognition();
    const generation = ++voice.generation;
    let handledFinal = false;
    recognition.lang = 'en-IN';
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      if (generation !== voice.generation) return;
      voice.listening = true;
      status('Listening… tell Jeevan what you need.', 'listening');
    };
    recognition.onresult = (event) => {
      if (generation !== voice.generation) return;
      const parts = Array.from(event.results).map((result) => result[0]?.transcript || '');
      const transcript = parts.join(' ').trim();
      nodes.heard.textContent = transcript;
      nodes.conversation.hidden = false;
      const hasFinal = Array.from(event.results).some((result) => result.isFinal);
      if (hasFinal && !handledFinal && transcript) {
        handledFinal = true;
        try { recognition.stop(); } catch (_) { /* already stopped */ }
        voice.listening = false;
        voice.busy = true;
        status('Understanding your request…', 'working');
        Promise.resolve(handleUtterance(transcript)).catch((error) => {
          showTurn(transcript, `I could not process that: ${error.message}`);
          app.toast(error.message, true);
          speak(`I could not process that. ${error.message}`, false);
        }).finally(() => { voice.busy = false; });
      }
    };
    recognition.onerror = (event) => {
      if (generation !== voice.generation || !voice.active) return;
      voice.listening = false;
      const denied = ['not-allowed', 'service-not-allowed', 'audio-capture'].includes(event.error);
      const message = denied
        ? 'Microphone or speech recognition is unavailable. Allow microphone access in Chrome or Edge, or use the editable request form.'
        : event.error === 'no-speech'
          ? 'I did not hear anything. Press Listen again when you are ready.'
          : `Speech recognition stopped (${event.error}). Press Listen again to retry.`;
      status(message, 'error');
      if (denied) app.toast(message, true);
    };
    recognition.onend = () => {
      if (generation !== voice.generation) return;
      voice.listening = false;
      if (!handledFinal && voice.active && nodes.indicator.dataset.mode === 'listening') {
        status('Listening stopped. Press Listen again to retry.', 'idle');
      }
    };
    voice.recognition = recognition;
    voice.listening = true;
    try { recognition.start(); }
    catch (error) { voice.listening = false; status(`Could not start microphone: ${error.message}`, 'error'); }
  }

  function fillDraft(draft) {
    const fields = {
      description: '#description', requested_date: '#requested-date',
      time_window: '#time-window', budget_rupees: '#budget', address: '#address'
    };
    Object.entries(fields).forEach(([key, selector]) => {
      document.querySelector(selector).value = draft[key] ?? '';
    });
  }

  async function interpret(transcript) {
    const generation = voice.generation;
    const result = await app.api('/api/v1/voice/interpret', {
      method: 'POST', body: JSON.stringify({ transcript, user_id: 'demo-user' })
    });
    if (!voice.active || generation !== voice.generation) return;
    voice.draft = result.draft;
    voice.ready = Boolean(result.ready);
    voice.missing = result.missing_fields || [];
    nodes.transcript.value = result.transcript;
    nodes.review.hidden = false;
    nodes.start.disabled = !voice.ready;
    fillDraft(result.draft);
    const reply = voice.ready ? `${result.reply} Should I start? Say yes or start task.` : result.reply;
    showTurn(result.transcript, reply);
    const missing = voice.missing.map((field) => missingLabels[field] || field).join('; ');
    status(voice.ready ? 'Ready for review. Say “yes” or “start task”, or press Start this task.' : `Please add: ${missing}.`, voice.ready ? 'ready' : 'working');
    speak(reply, true);
  }

  function speechCommand(transcript) {
    return transcript.toLowerCase().replace(/[.,!?]/g, '').replace(/\s+/g, ' ').trim();
  }

  async function handleUtterance(transcript) {
    const command = speechCommand(transcript);
    const task = app.getCurrentTask();
    if (command === 'stop listening') { stopSession(); return; }
    if (task && task.id === voice.taskId && !['completed', 'cancelled'].includes(task.status) && command === 'cancel task') {
      await actOnTask('cancel', task);
      return;
    }
    if (task && task.id === voice.taskId && task.status === 'awaiting_approval') {
      if (/^(approve|confirm|accept) (the |this |that )?(quote|price|booking)$/.test(command)) {
        await actOnTask('approve', task);
        return;
      }
      if (/^(decline|reject) (the |this |that )?(quote|price|booking)$/.test(command)) {
        await actOnTask('reject', task);
        return;
      }
      const reply = `Please say approve quote or decline quote. The quoted price is ${Math.round(task.quote_paise / 100)} rupees.`;
      showTurn(transcript, reply);
      speak(reply, true);
      return;
    }
    if (task && task.id === voice.taskId && task.status === 'retry_scheduled' && /^(retry|try again)( (the )?(call|task))?$/.test(command)) {
      await actOnTask('retry', task);
      return;
    }
    if (voice.ready && (/^(yes )?(start|begin|submit)( (the|my|this))? (task|booking)$/.test(command)
      || /^(yes,? )?(go ahead|do it|book it|please do it)$/.test(command)
      || /^(yes|yes please|correct|that's right|that is right)$/.test(command))) {
      await startReviewedTask();
      return;
    }
    if (voice.ready) {
      const reply = 'Your request is ready. Say yes or start task to proceed, or edit the transcript and select Update details.';
      showTurn(transcript, reply);
      speak(reply, true);
      return;
    }
    const combined = voice.missing.length && nodes.transcript.value.trim()
      ? `${nodes.transcript.value.trim()} ${transcript}` : transcript;
    await interpret(combined);
  }

  function describeTask(task) {
    if (task.status === 'awaiting_approval') {
      const price = Math.round(task.quote_paise / 100);
      const over = task.budget_paise ? Math.max(0, Math.round((task.quote_paise - task.budget_paise) / 100)) : 0;
      return `In this demo, ${task.provider_name} offered ${task.quoted_slot} for ${price} rupees${over ? `, ${over} rupees above your budget` : ''}. Say approve quote or decline quote. No booking will happen until you approve.`;
    }
    if (task.status === 'completed') return `The demo booking is complete. Your confirmation reference is ${task.confirmation_ref}.`;
    if (task.status === 'retry_scheduled') return 'The demo provider did not answer. Say retry call to try again.';
    if (task.status === 'needs_human') return 'This task needs an operator to review it. The details are in the operations view.';
    if (task.status === 'cancelled') return 'The task was cancelled. No booking was made.';
    return `The task is ${task.status.replaceAll('_', ' ')}. ${task.current_action || ''}`;
  }

  async function startReviewedTask() {
    if (!voice.ready || !voice.draft) return;
    pauseListening();
    const form = document.querySelector('#request-form');
    if (!form.reportValidity()) {
      status('Review the missing task fields before starting.', 'error');
      return;
    }
    voice.busy = true;
    voice.ready = false;
    nodes.start.disabled = true;
    status('Starting your task…', 'working');
    try {
      const payload = app.requestPayloadFromForm(voice.draft.service_type);
      if (!payload.budget_rupees || !payload.requested_date || !payload.time_window) throw new Error('Date, time window, and budget are required.');
      const task = await app.startTask(payload);
      voice.taskId = task.id;
      const reply = describeTask(task);
      showTurn(nodes.transcript.value, reply);
      status(task.status === 'awaiting_approval' ? 'Approval needed. Say “approve quote” or “decline quote”.' : `Task status: ${task.status.replaceAll('_', ' ')}.`, task.status === 'awaiting_approval' ? 'approval' : 'ready');
      speak(reply, ['awaiting_approval', 'retry_scheduled'].includes(task.status));
    } catch (error) {
      voice.ready = true;
      nodes.start.disabled = false;
      status(`Could not start task: ${error.message}`, 'error');
      app.toast(error.message, true);
    } finally { voice.busy = false; }
  }

  async function actOnTask(action, task) {
    pauseListening();
    voice.busy = true;
    const labels = { approve: 'Approving quote', reject: 'Declining quote', retry: 'Retrying call', cancel: 'Cancelling task' };
    status(`${labels[action]}…`, 'working');
    try {
      const updated = await app.performTaskAction(action, task.id, task.version);
      const reply = describeTask(updated);
      showTurn(nodes.heard.textContent, reply);
      status(`Task status: ${updated.status.replaceAll('_', ' ')}.`, updated.status === 'awaiting_approval' ? 'approval' : 'ready');
      speak(reply, ['awaiting_approval', 'retry_scheduled'].includes(updated.status));
    } catch (error) {
      app.toast(error.message, true);
      const reply = `That action failed: ${error.message}`;
      showTurn(nodes.heard.textContent, reply);
      speak(reply, true);
    } finally { voice.busy = false; }
  }

  nodes.toggle.addEventListener('click', () => {
    if (!Recognition) return;
    voice.active = true;
    nodes.stop.hidden = false;
    startListening();
  });
  nodes.stop.addEventListener('click', stopSession);
  nodes.reinterpret.addEventListener('click', async () => {
    const transcript = nodes.transcript.value.trim();
    if (!transcript) { status('Add a request before updating details.', 'error'); return; }
    voice.active = true;
    nodes.stop.hidden = false;
    pauseListening();
    voice.busy = true;
    status('Updating task details…', 'working');
    try { await interpret(transcript); }
    catch (error) { app.toast(error.message, true); status(error.message, 'error'); }
    finally { voice.busy = false; }
  });
  nodes.transcript.addEventListener('input', () => {
    voice.ready = false;
    nodes.start.disabled = true;
    status('Transcript changed. Select Update details before starting.', 'idle');
  });
  nodes.start.addEventListener('click', startReviewedTask);

  if (!Recognition) {
    nodes.toggle.disabled = true;
    nodes.toggle.textContent = 'Microphone unavailable';
    nodes.status.textContent = 'This browser does not support speech recognition. Open Jeevan in current Chrome or Edge, or use the request form below.';
  }
})();
