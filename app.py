# app.py
from flask import Flask, request, Response, url_for
from twilio.twiml.voice_response import VoiceResponse, Gather
import os, uuid

app = Flask(__name__)

# In-memory store for prototype. Keyed by CallSid.
CALL_STORE = {}

def make_summary(state):
    name = state.get('name','[unknown]')
    batch = state.get('batch','[unknown]')
    ctype = state.get('type','[unknown]')
    snippet = state.get('snippet','[no description]')
    return f"You are {name}. Batch {batch}. Complaint type {ctype}. Summary: {snippet}."

# === Primary incoming entrypoint (matches your Function path) ===
@app.route('/incoming-call-handler', methods=['POST'])
def incoming_call_handler():
    """Entry: welcome + ask batch number (this endpoint is what Twilio will call)"""
    resp = VoiceResponse()
    resp.say("Hello. Welcome to the complaint intake line. This call may be recorded for quality and investigation purposes.")
    g = Gather(input='speech dtmf', timeout=5, action=url_for('gather_batch', _external=True), method='POST')
    g.say("Please say your batch number after the tone. If you don't know it, say I don't know.")
    resp.append(g)
    resp.redirect(url_for('incoming_call_handler', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/gather-batch', methods=['POST'])
def gather_batch():
    callsid = request.values.get('CallSid')
    spoken = request.values.get('SpeechResult') or request.values.get('Digits') or ''
    CALL_STORE[callsid] = {'batch': spoken}
    valid = bool(spoken.strip()) and spoken.strip().lower() != "i don't know"
    resp = VoiceResponse()
    if valid:
        resp.say("Batch number confirmed. Thank you.")
        resp.redirect(url_for('ask_name', _external=True))
    else:
        g = Gather(input='speech dtmf', timeout=5, action=url_for('gather_batch', _external=True), method='POST')
        g.say("I could not confirm that batch number. Please repeat the batch number or press zero to speak to an operator.")
        resp.append(g)
    return Response(str(resp), mimetype='text/xml')

@app.route('/ask-name', methods=['POST'])
def ask_name():
    resp = VoiceResponse()
    g = Gather(input='speech dtmf', timeout=5, action=url_for('gather_name', _external=True), method='POST')
    g.say("Please state your full name after the tone. Say 'My name is' followed by your name.")
    resp.append(g)
    resp.redirect(url_for('ask_name', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/gather-name', methods=['POST'])
def gather_name():
    callsid = request.values.get('CallSid')
    name = request.values.get('SpeechResult') or request.values.get('Digits') or 'Unknown'
    CALL_STORE.setdefault(callsid, {})['name'] = name
    resp = VoiceResponse()
    resp.say(f"Thanks {name}.")
    resp.redirect(url_for('ask_type', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/ask-type', methods=['POST'])
def ask_type():
    resp = VoiceResponse()
    g = Gather(input='dtmf speech', num_digits=1, timeout=5, action=url_for('gather_type', _external=True), method='POST')
    g.say("Select complaint type. Press 1 for Billing, 2 for Service, 3 for Safety, 4 for Harassment, or say it now.")
    resp.append(g)
    resp.redirect(url_for('ask_type', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/gather-type', methods=['POST'])
def gather_type():
    callsid = request.values.get('CallSid')
    digit = request.values.get('Digits') or ''
    speech = request.values.get('SpeechResult') or ''
    mapping = {'1':'Billing','2':'Service','3':'Safety','4':'Harassment'}
    sel = mapping.get(digit, speech or 'Other')
    CALL_STORE.setdefault(callsid,{})['type'] = sel
    resp = VoiceResponse()
    resp.say("Now please describe your complaint in your own words after the beep. When finished press star.")
    resp.record(max_length=300, action=url_for('record_complete', _external=True),
                recording_status_callback=url_for('recording_callback', _external=True),
                finish_on_key='*', trim='trim-silence')
    return Response(str(resp), mimetype='text/xml')

@app.route('/recording-callback', methods=['POST'])
def recording_callback():
    # Twilio posts recording details here
    callsid = request.values.get('CallSid')
    recording_url = request.values.get('RecordingUrl')
    CALL_STORE.setdefault(callsid,{})['recording_url'] = recording_url
    # Later: call Deepgram or Twilio transcription and set CALL_STORE[callsid]['snippet']
    return ('', 204)

@app.route('/record-complete', methods=['POST'])
def record_complete():
    resp = VoiceResponse()
    resp.redirect(url_for('playback_confirm', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/playback-confirm', methods=['POST'])
def playback_confirm():
    callsid = request.values.get('CallSid')
    state = CALL_STORE.get(callsid, {})
    snippet = state.get('snippet') or 'A voice description has been recorded.'
    state['snippet'] = snippet
    summary = make_summary(state)
    resp = VoiceResponse()
    g = Gather(num_digits=1, action=url_for('confirm', _external=True), timeout=10)
    g.say(summary + " If this is correct press 1. To edit press 2.")
    resp.append(g)
    resp.redirect(url_for('playback_confirm', _external=True))
    return Response(str(resp), mimetype='text/xml')

@app.route('/confirm', methods=['POST'])
def confirm():
    callsid = request.values.get('CallSid')
    digit = request.values.get('Digits')
    resp = VoiceResponse()
    if digit == '1':
        cid = 'CMP-' + uuid.uuid4().hex[:8].upper()
        CALL_STORE.setdefault(callsid,{})['complaint_id'] = cid
        resp.say(f"Thank you. Your complaint ID is {cid}. We will send a confirmation SMS. Goodbye.")
        resp.hangup()
    else:
        resp.say("Okay. To re-record press 1. To change complaint type press 2. To speak to operator press 0.")
        g = Gather(num_digits=1, action=url_for('edit_options', _external=True), timeout=8)
        resp.append(g)
    return Response(str(resp), mimetype='text/xml')

@app.route('/edit-options', methods=['POST'])
def edit_options():
    d = request.values.get('Digits')
    resp = VoiceResponse()
    if d == '1':
        resp.say("Re-recording now.")
        resp.record(max_length=300, action=url_for('record_complete', _external=True), recording_status_callback=url_for('recording_callback', _external=True), finish_on_key='*')
    elif d == '2':
        resp.redirect(url_for('ask_type', _external=True))
    elif d == '0':
        resp.say("Transferring to operator.")
        # resp.dial('+911234567890')
    else:
        resp.say("Invalid option.")
        resp.redirect(url_for('playback_confirm', _external=True))
    return Response(str(resp), mimetype='text/xml')

# === Call status webhook (Twilio will POST events here) ===
@app.route('/sync-call-history', methods=['POST'])
def sync_call_history():
    # Twilio posts call progress events here (status, start/answer/hangup)
    callsid = request.values.get('CallSid')
    call_status = request.values.get('CallStatus')
    # store in call record for audit
    CALL_STORE.setdefault(callsid, {})['call_status'] = call_status
    # optional: persist to DB or Twilio Sync
    print(f"[sync] CallSid={callsid} status={call_status}")
    return ('', 204)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
