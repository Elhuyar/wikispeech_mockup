#-*- coding: utf-8 -*-
import sys, os, re
from tempfile import NamedTemporaryFile
from importlib import import_module
import requests
from flask import Flask, request, json, Response, make_response, render_template, redirect
from flask_cors import CORS

import wikispeech_server.config as config
from wikispeech_server.voice_config import textprocessor_configs, voice_configs

from wikispeech_server.options import *
import wikispeech_server.adapters.lexicon_client as lexicon_client
import wikispeech_server.log as log
from wikispeech_server.textprocessor import Textprocessor, TextprocessorException
from wikispeech_server.voice import Voice, VoiceException

import os.path
import datetime
import pytz
from pytz import timezone

import subprocess

#################
#
# Test opusenc before anything else
#
################

log.info("\nOPUSENC\n\nChecking that opusenc is installed on your system..")
retval = os.system("opusenc -V")
if retval != 0:
    os.system("opusenc -V")
    log.error("ERROR: opusenc was not found. You should probably run something like\nsudo apt install opus-tools\n")
    sys.exit(1)
else:
    log.info("opusenc found.\n\nEND OPUSENC\n")


###############
#
# Load textprocessors and voices
#
###############


textprocessors = []
def loadTextprocessor(tp_config):
    try:
        tp = Textprocessor(tp_config)        
        textprocessors.append(tp)
    except TextprocessorException as e:
        log.warning("Failed to load textprocessor from %s. Reason:\n%s" % (tp_config,e))

voices = []
def loadVoice(voice_config):
    try:
        v = Voice(voice_config)        
        voices.append(v)
    except VoiceException as e:
        log.warning("Failed to load voice from %s. Reason:\n%s" % (voice_config,e))



    

################
#
# Flask app
#
###############

app = Flask(__name__, static_url_path='')
CORS(app)









################################################################
#
# wikispeech api
#
# POST: curl -d "lang=en" -d "input=test." http://localhost:10000/
# GET:  curl "http://localhost:10000/?lang=en&input=test."

@app.route('/ping')
def ping():
    resp = make_response("wikispeech")
    resp.headers["Content-type"] = "text/plain"
    return resp


def versionInfo():
    res = []
    buildInfoFile = "/wikispeech/wikispeech_server/build_info.txt"
    if os.path.isfile(buildInfoFile):
        with open(buildInfoFile) as fp:  
            lines = fp.readlines()
            fp.close()
            for l in lines:
                res.append(l.strip())
                    
    else:
        res.append("Application name: wikispeech")
        res.append("Build timestamp: n/a")
        res.append("Built by: user")

        try:
            tag = subprocess.check_output(["git","describe","--tags"]).decode("utf-8").strip()
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]).decode("utf-8").strip()
            print(tag)
            print(branch)
            res.append( ("Release: %s on branch %s") % (tag, branch) )
        except:
            log.warning("couldn't retrieve git release info: %s" % sys.exc_info()[1])
            res.append("Release: unknown");

    res.append("Started: " + startedAt)
    return res
    


def genStartedAtString():
    from time import strftime, gmtime
    from tzlocal import get_localzone
    local_tz = get_localzone()
    now = datetime.datetime.now()
    if local_tz != None:
        now = now.replace(tzinfo=local_tz)
    now = now.astimezone(pytz.utc)
    return '{:%Y-%m-%d %H:%M:%S %Z}'.format(now)

startedAt = genStartedAtString()
vInfo = versionInfo()

@app.route('/version')
def version():
    resp = make_response("\n".join(vInfo))
    resp.headers["Content-type"] = "text/plain"
    return resp
    


@app.route('/', methods=["OPTIONS"])
def wikispeech_options():

    options = getWikispeechOptions()
    log.debug(options)
    resp = make_response(json.dumps(options))
    resp.headers["Content-type"] = "application/json"
    resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
    return resp

@app.route('/options', methods=["GET", "POST"])
def wikispeech_options2():
    options = getWikispeechOptions()
    log.debug(options)
    resp = make_response(json.dumps(options))
    resp.headers["Content-type"] = "application/json"
    resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
    return resp


@app.route('/languages', methods=["GET"])
def list_languages():
    json_data = json.dumps(getSupportedLanguages())
    return Response(json_data, mimetype='application/json')

@app.route('/', methods=["GET", "POST"])
@app.route('/wikispeech', methods=["GET", "POST"])
def wikispeech():
    global hostname

    from urllib.parse import urlparse
    parsed_uri = urlparse(request.url)
    hostname = '{uri.scheme}://{uri.netloc}/'.format(uri=parsed_uri)

    # log.debug("request.url: %s" % hostname)
    log.info("request: %s" % request)
    log.info("request.url: %s" % request.url)
    log.info("hostname: %s" % hostname)
    if not hostname.endswith("/"):
        hostname = hostname+"/"
    if "wikispeech.morf.se" in hostname: ## HL 20171121: force https for wikispeech.morf.se
        hostname = hostname.replace("http://","https://")
    log.debug("hostname: %s" % hostname)
        
    lang = getParam("lang")
    input = getParam("input")
    input_type = getParam("input_type", "text")
    output_type = getParam("output_type", "json")


    #For use with synthesis only
    presynth = getParam("presynth", False)
    if presynth == "True":
        presynth = True
    else:
        presynth = False



    textprocessor_name = getParam("textprocessor", "default_textprocessor")
    voice_name = getParam("voice", "default_voice")



    log.debug("WIKISPEECH CALL - LANG: %s, INPUT_TYPE: %s, OUTPUT_TYPE: %s, INPUT: %s" % (lang, input_type, output_type, input))

    supported_languages = getSupportedLanguages()

    if not lang or not input:
        return render_template("usage.html", server=hostname, languages=supported_languages)

    if lang not in supported_languages:
        return "Language %s not supported. Supported languages are: %s" % (lang, supported_languages)


    if input == "TEST_EXAMPLE":
        return json.dumps(getTestExample(lang))


    if input_type in ["text","ssml"]:
        markup = textproc(lang, textprocessor_name, input, input_type=input_type)
        if type(markup) == type(""):
            log.debug("RETURNING MESSAGE: %s" % markup)
            return markup
    else:
        return "input_type %s not supported" % input_type

    if output_type == "json":
        result = synthesise(lang, voice_name, markup,"markup",output_type, hostname=hostname, presynth=presynth)
        if type(result) == type(""):
            log.debug("RETURNING MESSAGE: %s" % result)
            return result

        #TODO
        #The player being developed at wikimedia depends on the output matching input exactly
        #phabricator T147547 
        #Some special characters, like "—" (em-dash) aren't returned properly by the TTS-server. This breaks the token-to-HTML mapping, since it relies on finding the exact same strings in the HTML as the tokens orth values.
        #Add a test for that here,
        #And then require adapter components to conform to this?
        #how, exactly ...
        msg = checkInputAndOutputTokens(input,result["tokens"])
        if msg:
            result["message"] = msg



        json_data = json.dumps(result)
        return Response(json_data, mimetype='application/json')

    else:
        return "output_type %s not supported" % output_type




def getSupportedLanguages():
    supported_languages = []
    for lang in textprocSupportedLanguages():
        if lang in synthesisSupportedLanguages():
            supported_languages.append(lang)
    return supported_languages




##############################################
#
# textprocessing api
#
# POST: curl -d "lang=en" -d "input=test." http://localhost:10000/textprocessing/
# GET:  curl "http://localhost:10000/textprocessing/?lang=en&input=test."
#





@app.route('/textprocessing/languages', methods=["GET"])
def list_textprocSupportedLanguages():
    json_data = json.dumps(textprocSupportedLanguages())
    return Response(json_data, mimetype='application/json')

@app.route('/textprocessing/textprocessors', methods=["GET"])
def list_textprocessors():
    """Returns list of loaded textprocessors."""
    t = []
    for tp in textprocessors:
        for tpc in textprocessor_configs:
            if tpc["name"] == tp.name:
                t.append(tpc)
    json_data = json.dumps(t)
    return Response(json_data, mimetype='application/json')

@app.route('/textprocessing/textprocessors/<lang>', methods=["GET"])
def return_tp_configs_by_language(lang):
    json_data = json.dumps(list_tp_configs_by_language(lang))
    return Response(json_data, mimetype='application/json')

def list_tp_configs_by_language(lang):
    l = []
    for tp in textprocessors:
        if tp.lang == lang:
            l.append(tp.config)
    return l

def get_tp_config_by_name(name):
    for tp in textprocessors:
        log.debug("get_tp_config_by_name: %s" % tp)
        log.debug("name: %s, wanted: %s" % (tp.name, name))
        if tp.name == name:
            log.debug("RETURNING: %s" % tp.config)
            return tp.config
    return None

def list_tp_configs_by_languageOLD(lang):
    l = []
    for tp_config in textprocessor_configs:
        if tp_config["lang"] == lang:
            l.append(tp_config)
    return l

def get_tp_config_by_nameOLD(name):
    for tp_config in textprocessor_configs:
        log.debug("get_tp_config_by_name: %s" % tp_config)
        log.debug("name: %s, wanted: %s" % (tp_config["name"], name))
        if tp_config["name"] == name:
            log.debug("RETURNING: %s" % tp_config)
            return tp_config
    return None




@app.route('/textprocessing/', methods=["OPTIONS"])
def textprocessing_options():

    options = getTextprocessingOptions()
    resp = make_response(json.dumps(options))
    resp.headers["Content-type"] = "application/json"
    resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
    return resp



@app.route('/textprocessing/', methods=["GET", "POST"])
def textprocessing():
    lang = getParam("lang")
    textprocessor_name = getParam("textprocessor", "default_textprocessor")
    input_type = getParam("input_type", "text")
    output_type = getParam("output_type", "json")
    input = getParam("input")

    if lang == None or input == None:
        options = getTextprocessingOptions()
        resp = make_response(json.dumps(options))
        resp.headers["Content-type"] = "application/json"
        resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
        return resp

    
    if input_type in ["text","ssml"]:
        markup = textproc(lang,textprocessor_name, input, input_type=input_type)
        if type(markup) == type(""):
            log.debug("RETURNING MESSAGE: %s" % markup)
            return markup
    else:
        return "input_type %s not supported" % input_type

    if output_type == "json":
        json_data = json.dumps(markup)
        return Response(json_data, mimetype='application/json')
    else:
        return "output_type %s not supported" % output_type


def textprocSupportedLanguages_OLD():
    supported_languages = []
    for t in textprocessor_configs:
        if t["lang"] not in supported_languages:
            supported_languages.append(t["lang"])
    return supported_languages
#HB 170413 Changed to look at list of loaded textprocessors, instead of textprocessor_configs
def textprocSupportedLanguages():
    supported_languages = []
    for t in textprocessors:
        if t.lang not in supported_languages:
            supported_languages.append(t.lang)
    return supported_languages

def textproc(lang, textprocessor_name, text, input_type="text"):

    tp_configs = list_tp_configs_by_language(lang)
    textprocessor = None
    if textprocessor_name == "default_textprocessor":
        for tp in tp_configs:
            if tp["lang"] == lang:
                textprocessor = tp
                break
        if textprocessor == None:
            return "ERROR: No textprocessor available for language %s" % lang
    else:
        for tp in tp_configs:
            if tp["name"] == textprocessor_name:
                textprocessor = tp
                break
        if textprocessor == None:
            #example http://localhost/?lang=sv&input=test&textprocessor=undefined
            return "ERROR: Textprocessor %s not defined for language %s" % (textprocessor_name, lang)


    log.debug("TEXTPROCESSOR: %s" % textprocessor)

    for component in textprocessor["components"]:

        module_name = component["module"]
        component_name = component["call"]

        log.debug("MODULE: %s" % module_name)
        log.debug("COMPONENT: %s" % component_name)

        #Import the defined module and function
        #mod = import_module(module_name)
        #HB testing
        mod = import_module("wikispeech_server."+module_name)
        #log.debug(mod)
        #log.debug(dir(mod))
        process = getattr(mod, component_name)
        log.debug("PROCESS: %s" % process)

        #TODO clean this up to always use process(utt)
        if component_name == "tokenise":
            utt = process(text,lang=lang)
            utt["lang"] = lang
            utt["original_text"] = text
            #Simple mechanism to do only tokenisation
            #Build on this to do partial processing in other ways
            if getParam("process", "none") == "tokenise":
                return utt

        elif component_name == "marytts_preproc":
            utt = process(text, lang, component, input_type=input_type)
        else:
            try:
                utt = process(utt)
            except:
                utt = process(utt, lang, component)
        log.debug(str(utt))

    return utt





###################################################################################
#
# synthesis api
#
# POST: curl -d "lang=en" -d "input={"s": {"phrase": {"boundary": {"@breakindex": "5", "@tone": "L-L%"}, "t": [{"#text": "test", "@accent": "!H*", "@g2p_method": "lexicon", "@ph": "' t E s t", "@pos": "NN", "syllable": {"@accent": "!H*", "@ph": "t E s t", "@stress": "1", "ph": [{"@p": "t"}, {"@p": "E"}, {"@p": "s"}, {"@p": "t"}]}}, {"#text": ".", "@pos": "."}]}}}" http://localhost:10000/textprocessing/
# GET:  curl 'http://localhost:10000/textprocessing/?lang=en&input={"s": {"phrase": {"boundary": {"@breakindex": "5", "@tone": "L-L%"}, "t": [{"#text": "test", "@accent": "\!H\*", "@g2p_method": "lexicon", "@ph": "\' t E s t", "@pos": "NN", "syllable": {"@accent": "\!H\*", "@ph": "t E s t", "@stress": "1", "ph": [{"@p": "t"}, {"@p": "E"}, {"@p": "s"}, {"@p": "t"}]}}, {"#text": ".", "@pos": "."}]}}}'
#
#
#
#curl  -X POST -H "Content-Type: application/json" -d "lang=en" -d 'input={"s":{"phrase":{"boundary":{"@breakindex":"5","@tone":"L-L%"},"t":[{"#text":"test","@g2p_method":"lexicon","@ph":"\'+t+E+s+t","@pos":"NN","syllable":{"@ph":"t+E+s+t","@stress":"1","ph":[{"@p":"t"},{"@p":"E"},{"@p":"s"},{"@p":"t"}]}},{"#text":".","@pos":"."}]}}}' http://localhost:10000/textprocessing/

#curl -X POST -H "Content-Type: application/json" -d '{"key":"val"}' URL
#curl -X POST -H "Content-Type: application/json" -d "lang=en" --data-binary @test.json http://localhost:10000/synthesis/

#nej ingen av dessa funkar..

@app.route('/synthesis/languages', methods=["GET"])
def list_synthesisSupportedLanguages():
    json_data = json.dumps(synthesisSupportedLanguages())
    return Response(json_data, mimetype='application/json')


@app.route('/synthesis/voices', methods=["GET"])
def list_voices():
    """Returns list of loaded voices."""
    v = []
    for voice in voices:
        for vc in voice_configs:
            if vc["name"] == voice.name:
                v.append(vc)
    json_data = json.dumps(v)
    return Response(json_data, mimetype='application/json')

@app.route('/synthesis/voices/<lang>', methods=["GET"])
def return_voices_by_language(lang):
    json_data = json.dumps(list_voices_by_language(lang))
    return Response(json_data, mimetype='application/json')

def list_voices_by_language(lang):
    v = []
    for voice in voices:
        if voice.lang == lang:
            v.append(voice.config)
    return v

def list_voices_by_languageOLD(lang):
    v = []
    for voice in voice_configs:
        if voice["lang"] == lang:
            v.append(voice)
    return v

def synthesisSupportedLanguages_OLD():
    langs = []
    for voice in voice_configs:
        if voice["lang"] not in langs:
            langs.append(voice["lang"])
    return langs

def synthesisSupportedLanguages():
    langs = []
    for voice in voices:
        if voice.lang not in langs:
            langs.append(voice.lang)
    return langs



@app.route('/synthesis/', methods=["OPTIONS"])
def synthesis_options():

    options = getSynthesisOptions()
    resp = make_response(json.dumps(options))
    resp.headers["Content-type"] = "application/json"
    resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
    return resp




@app.route('/synthesis/', methods=["GET","POST"])
def synthesis():
    hostname = request.url_root

    lang = getParam("lang")
    input = getParam("input")
    voice_name = getParam("voice", "default_voice")
    input_type = getParam("input_type", "markup")
    output_type = getParam("output_type", "json")
    presynth = getParam("presynth", False)


    if lang == None or input == None:
        options = getSynthesisOptions()
        resp = make_response(json.dumps(options))
        resp.headers["Content-type"] = "application/json"
        resp.headers["Allow"] = "OPTIONS, GET, POST, HEAD"
        return resp





    if presynth == "True":
        presynth = True
    else:
        presynth=False

    #log.debug "SYNTHESIS CALL - LANG: %s, INPUT_TYPE: %s, OUTPUT_TYPE: %s, INPUT: %s" % (lang, input_type, output_type, input)

    if lang not in synthesisSupportedLanguages():
        return "synthesis does not support language %s" % lang

    #The input is a json string, needs to be a python dictionary
    input = json.loads(input)
    result = synthesise(lang,voice_name,input,input_type,output_type,hostname=hostname,presynth=presynth)
    if type(result) == type(""):
        log.debug("RETURNING MESSAGE: %s" % result)
        return result
    json_data = json.dumps(result)
    return Response(json_data, mimetype='application/json')


def synthesise(lang,voice_name,input,input_type,output_type,hostname="http://localhost/", presynth=False):

    #presynth for use with marytts WIKISPEECH_JSON output type
    #presynth = True


    #if input_type not in ["markup","transcription"]:
    if input_type not in ["markup"]:
        return "Synthesis cannot handle input_type %s" % input_type

    ##if input_type == "transcription":
        

    
    voices = list_voices_by_language(lang)
    #log.debug(voices)
    voice = None
    if voice_name == "default_voice":
        if len(voices) > 0:
            voice = voices[0]
        if voice == None:
            return "No voice available for language %s" % lang
    else:
        for v in voices:
            if v["name"] == voice_name:
                voice = v
        if voice == None:
            return "ERROR: voice %s not defined for language %s." % (voice_name, lang)




    #log.debug(voice)

    #Import the defined module and function
    #TODO drop synthesise for voice[function] (?)

    mod = import_module("wikispeech_server."+voice["adapter"])
    log.debug(str(mod))
    log.debug(str(dir(mod)))

    process = getattr(mod, "synthesise")
    
    log.debug("PROCESS: %s" % process)

    #process = getattr(__import__(voice["adapter"]), "synthesise")



    (audio_file, output_tokens) = process(lang, voice, input, presynth=presynth, hostname=hostname)

    #Get audio from synthesiser, convert to opus, save locally, return url
    #TODO return wav url also? Or client's choice?
    if output_type != "test":
        audio_file = saveAndConvertAudio(audio_file, presynth)


    #audio_url = "%s/%s" % (config.config.get("Audio settings","audio_url_prefix"), audio_file)
    #HB T180685: Remove audio_url_prefix from wikispeech config
    audio_url = "%s%s/%s" % (hostname, "audio", audio_file)
    log.debug("audio_url: %s" % audio_url)


    data = {
        "audio":audio_url,
        "tokens":output_tokens
    }

    
    return data


############################################
#
#  serve the audio file if needed (should usually be behind proxy)
from flask import send_from_directory

@app.route('/audio/<path:path>')
def static_proxy_audio(path):
    audio_tmpdir = config.config.get("Audio settings","audio_tmpdir")
    audio_file_name = audio_tmpdir+"/"+path
    log.info("Looking for audio file %s" % audio_file_name)
    # send_static_file will guess the correct MIME type
    #return send_from_directory("tmp", path)
    return send_from_directory(os.getcwd()+"/"+audio_tmpdir, path)

############################################
#
#  serve test file if needed (should usually be behind proxy)

@app.route('/test.html')
@app.route('/wikispeech/test.html')
def static_test():
    log.info("Looking for static file %s" % "test.html")
    #HB this is wrong (won't work on morf)
    #hostname = "http://localhost:10000"
    hostname = request.url_root
    return render_template("test.html", server=hostname)


@app.route('/wikispeech_simple_player.js')
def static_proxy_js():
    filename = "wikispeech_simple_player.js"
    root_dir = os.getcwd()
    log.info("Looking for static file %s/%s" % (root_dir, filename))
    return send_from_directory(root_dir, filename)


@app.route('/workflow_demo/<path:path>')
def static_proxy_workflow(path):
    filename = "workflow_demo/"+path
    root_dir = os.getcwd()
    log.info("Looking for static file %s/%s" % (root_dir, filename))
    return send_from_directory(root_dir, filename)





##############################################
#
#   Connection to lexicon server running on same machine
#   Used in workflow demo. Other uses?

from flask import stream_with_context

@app.route('/lexserver/<path:url>')
def lexserver_proxy(url):
    lexicon_host = config.config.get("Services","lexicon")
    redirect_url = "%s/%s%s" % ((lexicon_host, url, "?" + request.query_string.decode("utf-8") if request.query_string else ""))
    log.info("Lexserver proxy to: %s" % redirect_url)
    req = requests.get(redirect_url, stream = True)
    return Response(stream_with_context(req.iter_content()), content_type = req.headers['content-type'])




###################################################################
#
# various stuff
#


def checkInputAndOutputTokens(input_string,output_token_list):
    msgs = []
    for token in output_token_list:
        log.debug(token)
        if token["orth"] not in input_string:
            msgs.append("output token \"%s\" not found in input string \"%s\"" % (token["orth"], input_string))

            
    #attempt to correct ...
    if len(msgs) > 0:
        input_string = re.sub(r"\s*([,.?!\"()])\s*",r" \1 ", input_string)
        input_string = re.sub(r"\s+", r" ", input_string)
        input_string = input_string.strip()
        
        input_list = input_string.split(" ")
        output_list = [elem["orth"] for elem in output_token_list if elem["orth"] != ""]
        if len(input_list) != len(output_list):
            msgs.append("WARNING: Unable to correct output token list. Input contains %d tokens, output contains %d non-empty tokens." % (len(input_list), len(output_list)))
            msgs.append("input token list : %s" % input_list)
            msgs.append("output token list: %s" % output_list)
        else:
            i = 0
            j = 0
            while i < len(input_list) and j < len(output_token_list):
                input_orth = input_list[i]
                output_orth = output_token_list[j]["orth"]
                #output_orth = output_list[i]
                if output_orth == "":
                    j += 1
                    log.debug("skipping empty output token")
                else:
                    log.debug("%s\t%s" % (input_orth, output_orth))
                    if input_orth != output_orth:
                        output_token_list[j]["orth"] = input_orth
                        msgs.append("REPLACED: %s -> %s" % (output_orth, input_orth))
                    i += 1
                    j += 1
                                
                        
            
    return msgs




def saveAndConvertAudio(audio_url,presynth=False):
    global config

    log.debug("PRESYNTH: %s, type: %s" % (presynth, type(presynth)) )

    tmpdir = config.config.get("Audio settings","audio_tmpdir")
    log.debug("TMPDIR: %s" % tmpdir)
    
    fh = NamedTemporaryFile(mode='w+b', dir=tmpdir, delete=False)
    tmpwav = fh.name    
    
    if presynth:
        fh.close()
        #The "url" is actually a filename at this point
        cmd = "mv %s %s" % (audio_url, tmpwav)
        log.debug(cmd)
        os.system(cmd)

    else:

        log.debug("audio_url:\n%s" % audio_url)
        r = requests.get(audio_url)
        log.debug(r.headers['content-type'])

        audio_data = r.content

        fh = NamedTemporaryFile(mode='w+b', dir=tmpdir, delete=False)
        tmpwav = fh.name    

        fh.write(audio_data)
        fh.close()

    #tmpwav is now the synthesised wav file
    #tmpopus = "%s/%s.opus" % (tmpdir, tmpfilename)
    tmpopus = "%s.opus" % tmpwav

    convertcmd = "opusenc %s %s" % (tmpwav, tmpopus)
    log.debug("convertcmd: %s" % convertcmd)
    if log.log_level != "debug":
        convertcmd = "opusenc --quiet %s %s" % (tmpwav, tmpopus)
    retval = os.system(convertcmd)
    if retval != 0:
        log.error("ERROR: opusenc was not found. You should probably run something like\nsudo apt install opus-tools\n")

    #remove everything before the tmpdir, to build the external url
    #HB problem with wikimedia usage?
    #opus_url_suffix = re.sub("^.*/%s/" % tmpdir, "%s/" % tmpdir, tmpopus)
    opus_url_suffix = re.sub("^.*/%s/" % tmpdir, "", tmpopus)
    log.debug("opus_url_suffix: %s" % opus_url_suffix)

    #return tmpopus
    return opus_url_suffix


def getTestExample(lang):
    if lang == "en":
        return {"tokens": [["sil", "0.197"], ["this", "0.397"], ["is", "0.531"], ["a", "0.587"], ["test", "0.996"], ["sil", "1.138"]], "audio": "https://morf.se/flite_test/tmp/flite_tmp.wav"}
    elif lang == "hi":
        return {"tokens": [["sil", "0.186"], ["\u0928\u091c\u093c\u0930", "0.599"], ["\u0906\u0924\u093e", "0.905"], ["\u0939\u0948\u0964", "1.134"], ["sil", "1.384"], ["sil", "1.564"], ["\u0907\u0938\u0940", "1.871"], ["\u0915\u093e\u0930\u0923", "2.39"], ["sil", "2.565"]], "audio": "https://morf.se/flite_test/tmp/flite_tmp.wav"}
    else:
        return "No test example found for %s" % lang



def getParam(param,default=None):
    value = None
    log.debug("getParam %s, request.method: %s" % (param, request.method))
    if request.method == "GET":
        value = request.args.get(param)
    elif request.method == "POST":
        #log.debug(request)
        #log.debug(request.form)
        if param in request.form:
            value = request.form[param]
    log.debug("VALUE: %s" % value)
    if value == None:
        value = default
    return value





#########################
#
# Tests
#
#########################






def test_lexicon_client():
    lexicon = "wikispeech_testdb:sv"
    sent = "apa hund färöarna"
    trans = {}
    trans["apa"] = '"" A: . p a'
    trans["hund"] = '" h u0 n d'
    trans["färöarna"] = '"" f {: . % r 2: . a . rn a'

    try:
        lexicon_client.loadLexicon(lexicon)
        lex = lexicon_client.getLookupBySentence(sent, lexicon)
        log.debug("LEX: %s" % lex)
    except:
        log.error("Failed to do lexicon lookup.\nError type: %s\nError info:%s" % (sys.exc_info()[0], sys.exc_info()[1]))

        import traceback
        log.debug("Stacktrace:")
        if log.log_level == "debug":
            traceback.print_tb(sys.exc_info()[2])
        log.debug("END stacktrace")

        log.error("lexicon lookup test failure")
        log.error("No running lexserver found at %s" % config.config.get("Services","lexicon"))
        raise
        
    for word in sent.split(" "):
        try:
            if lex[word] != trans[word]:
                log.error("lexicon lookup test failure")
                log.error("word %s, found %s, expected %s" % (word, lex[word], trans[word]))
                raise
        except KeyError:
            log.error("Lexicon lookup test failure: Word %s not found in lexicon %s" % (word, lexicon))
            raise
            
                
    log.debug("SUCCESS: lexicon lookup test")


def test_textproc():
    sent = "apa"
    try:
        res = textproc("sv","default_textprocessor", sent)
    except:
        log.error("Failed to do textprocessing.\nError type: %s\nError info:%s" % (sys.exc_info()[0], sys.exc_info()[1]))

        import traceback
        log.debug("Stacktrace:")
        traceback.print_tb(sys.exc_info()[2])
        log.debug("END stacktrace")

        log.error("textprocessing test failure")
        log.error("No running marytts server found at %s" % config.config.get("Services","marytts"))
        raise
        
        
    #TODO Better with exception than return value
    if type(res) == type("") and res.startswith("ERROR:"):
        log.error("Failed to do textprocessing")
        log.error(res)
        log.error("textprocessing test failure")
        raise
        
    log.debug("%s --> %s" % (sent,res))
    log.debug("SUCCESS: textprocessing test")

    
def test_wikispeech():
    sent = "apa"
    trans = {}
    trans["apa"] = '" A: - p a'
    lang = "sv"
    try:
        tmp = textproc(lang,"default_textprocessor", sent)
        res = synthesise(lang,"default_voice",tmp,"markup","json")
    except FileNotFoundError:
        log.error("Failed to do wikispeech test.\nError type: %s\nError info:%s" % (sys.exc_info()[0], sys.exc_info()[1]))

        import traceback
        log.debug("Stacktrace:")
        traceback.print_tb(sys.exc_info()[2])
        log.debug("END stacktrace")

        log.error("wikispeech test failure")
        log.error("Is the audio_tmpdir %s correctly configured?" % config.config.get("Audio settings", "audio_tmpdir"))
        raise
        
    except:
        log.error("Failed to do wikispeech test.\nError type: %s\nError info:%s" % (sys.exc_info()[0], sys.exc_info()[1]))

        import traceback
        log.debug("Stacktrace:")
        traceback.print_tb(sys.exc_info()[2])
        log.debug("END stacktrace")

        log.error("wikispeech test failure")
        log.error("No running marytts server found at %s" % config.config.get("Services","marytts"))
        raise

    #TODO Better with exception than return value
    if type(res) == type("") and res.startswith("No voice available"):
        log.error("Failed to do wikispeech test")
        log.error(res)
        log.error("wikispeech test failure")
        raise
        
    log.debug("%s --> %s" % (sent,res))
    log.debug("SUCCESS: wikispeech test")


def test_config():
    log.debug("\nTEST CONFIG\n")

    log.debug("Testing that audio_tmpdir exists and is writeable")
    try:
        tmpdir = config.config.get("Audio settings","audio_tmpdir")
        log.debug("TMPDIR: %s" % tmpdir)
        fh = NamedTemporaryFile(mode='w+b', dir=tmpdir, delete=False)
        tmpfile = fh.name        
        fh.write("test".encode("utf-8"))
        fh.close()
    except:
        log.error("audio_tmpdir does not exist or is not writeable")
        raise



    log.debug("Testing to make sure that config file contains url to lexicon server:")
    try:
        assert ( config.config.has_option("Services", "lexicon") == True )
        log.debug("Services|lexicon = %s" % config.config.get("Services", "lexicon"))
        log.debug("ok")
    except:
        log.error("Services|lexicon not found in config file\n")
        raise
    log.debug("\nEND TEST CONFIG\n")
        

    

if __name__ == '__main__':
    print("use wikispeech_mockup/bin/wikispeech to run")


