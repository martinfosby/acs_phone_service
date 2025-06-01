import logging
import os
import azure.functions as func
import requests
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceExistsError, ClientAuthenticationError
from datetime import datetime, timezone
from azure.communication.identity import CommunicationIdentityClient
from CallAutomationSingleton import CallAutomationSingleton
from AsyncCallAutomationSingleton import AsyncCallAutomationSingleton
import asyncio
from azure.storage.blob import ContentSettings
from azure.communication.chat import ChatClient
import threading

# user functions
from utility import *
from config import *
import globals

running_tasks = {}  # global or class-level dict

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.event_grid_trigger(arg_name="event")
@app.function_name(name="PhoneRecordEventGridTrigger")
def phone_record_event_grid_trigger(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    logging.info(f"Event received at: {datetime.now(timezone.utc)}, event time: {event.event_time}")
    event_data = event.get_json()
    logging.info(f'Event data: {event_data}')
    event_type = event.event_type

     # Handle subscription validation
    if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
        logging.info('Subscription validation event received')
        validation_code = event_data["validationCode"]
        logging.info(f"Validating EventGrid subscription with code: {validation_code}")
        return {
            "validationResponse": validation_code
        }
    
    if event_type == "Microsoft.Communication.IncomingCall":
        logging.info('Incoming call event received')
        call_automation_client = CallAutomationSingleton.get_instance(acs_connection_string)
        # call_automation_client = get_call_automation_client()
        logging.info(f'Created call_automation_client {call_automation_client}')
        call_connection_properties = call_automation_client.answer_call(event_data.get("incomingCallContext"), callback_url)
        logging.info(f'Answered call with connection properties: {call_connection_properties}')
        

    if event_type == "Microsoft.Communication.RecordingFileStatusUpdated":
        logging.info('Recording file status updated event received')
        recording_data_and_call_data = {
                **event_data,
                **globals.call_data,
                "json_data_from_telephone": True
        }
        logging.info(f"recording_data_and_call_data: {recording_data_and_call_data}")
        if os.getenv("USE_WEBAPP") == "true":
            logging.info("Using webapp")
            try:
                res = requests.post(os.getenv("WEBAPP_URL") + "/transcribe", json=recording_data_and_call_data)
                logging.info(f"webapp response: {res.status_code}, {res.text}")
            except requests.exceptions.RequestException as e:
                logging.error(f"Error sending webapp: {e}")
        else:
            logging.info("Not using webapp")
            logging.info("Uploading recording to blob storage...")
            blob_service_client = BlobServiceClient(
                account_url="https://stfeilmelding001.blob.core.windows.net", 
                credential=default_credential if os.getenv("CLOUD_ENV") == "azure" else named_key_credential)
            
            container_name = "recording-and-call-data"
            try:
                container_client = blob_service_client.create_container(name=container_name)
            except ResourceExistsError:
                logging.info("Container already exists")

            blob_client: BlobClient = blob_service_client.get_blob_client(container=container_name, blob=event_data.get("recordingId") + ".json")
            
            

            blob_client.upload_blob(
                json.dumps(recording_data_and_call_data, indent=4),
                metadata={
                    "processed": "false",
                    "language": "nb-NO",
                    "priority": "high",
                    "transcribed": "false"
                },
                content_settings=ContentSettings(content_type='application/json')
            )




@app.route(route="callback", auth_level=func.AuthLevel.ANONYMOUS)
@app.function_name(name="Callback")
def callback(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('ACS callback triggered.')
    try:
        data = req.get_json()

        # Hvis det er en liste, iterer over hvert event
        events = data if isinstance(data, list) else [data]

        for event in events:
            event_type = event.get("type")
            logging.info(f"Received event: {event_type} with data: {event}")


            if event_type == "Microsoft.Communication.AddParticipantSucceeded":
                logging.info('AddParticipantSucceeded event received')
                call_connection_id = event.get("data").get("callConnectionId")
                logging.info(f"Participant added to call with ID: {call_connection_id}")


            elif event_type == "Microsoft.Communication.CallConnected":
                # Safely get the call connection ID
                call_connection_id = event.get("data", {}).get("callConnectionId")
                logging.info(f"Call is now connected. ID: {call_connection_id}")

                # Add timer to stop the call after a delay
                threading.Thread(target=stop_call_after_delay, args=(call_connection_id, 600)).start()


                # Configure the call automation client
                AsyncCallAutomationSingleton.configure(acs_connection_string)

                
                async def handle_connection():
                    client = AsyncCallAutomationSingleton.get_new_client()
                    logging.info('Started handling connection asynchronously')
                    try:
                        dtmf_task = asyncio.create_task(
                            AsyncCallAutomationSingleton.start_continous_dtmf_recognition(
                                client=client,
                                call_connection_id=call_connection_id,
                                operation_context="call-app-continuous-dtmf"
                            )
                        )
                        audio_task = asyncio.create_task(
                            AsyncCallAutomationSingleton.audio_playback_to_all(
                                client=client,
                                call_connection_id=call_connection_id,
                                operation_context="instruksjoner",
                                callback_url=callback_url,
                                container_name="audio-for-playback",
                                blob_name="instruksjoner.wav"
                            )
                        )
                        running_tasks[call_connection_id] = [dtmf_task, audio_task]
                        # Wait for both tasks to complete
                        await asyncio.gather(dtmf_task, audio_task)

                        logging.info('Started continuous DTMF recognition and audio playback')
                    except Exception as e:
                        logging.error(f"Error during handling connection: {e}")
                    finally:
                        await client.close()

                asyncio.run(handle_connection())
                

            

            elif event_type == "Microsoft.Communication.RecognizeCompleted":
                logging.info('RecognizeCompleted event received')
                operation_context = event.get("data").get("operationContext")
                logging.info(f"Recognize operation completed with context: {operation_context}")
                result_information = event.get("data").get("resultInformation")
                logging.info(f"Result information: {result_information}")
                dtmf_result = event.get("data").get("dtmfResult")
                tones = dtmf_result.get("tones")
                logging.info(f"DTMF Tones: {tones}")
                
                if operation_context == "recognize-employee-id":
                    call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
            
                    # Start audio playback
                    try:
                        audio_playback_to_all(call_connection_client, 
                                            operation_context="denne-samtalen-blir-tatt-opp-deretter-transkribert",
                                            callback_url=callback_url,
                                            container_name="audio-for-playback", 
                                            blob_name="denne-samtalen-blir-tatt-opp-deretter-transkribert.wav")
                    except Exception as e:
                        logging.error(f"Failed to start audio playback after RecognizeCompleted: {e}")


                    call_properties = call_connection_client.get_call_properties()
                    logging.info(f"Call properties: {call_properties}")

                    try:
                        call_data = interpret_dtmf(tones, call_properties)
                        globals.call_data = call_data
                        upload_interpret_dtmf(call_data=call_data, call_properties=call_properties)
                    except Exception as e:
                        logging.error(f"Failed to interpret DTMF: {e}")

            elif event_type == "Microsoft.Communication.RecognizeFailed":
                logging.info('RecognizeFailed event received')
                logging.warning(f"Warning RecognizeFailed: {event.get('data').get('resultInformation')}")

                call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                                                                                            
                try:
                    # Try to recognize DTMF without delegation key
                    recognize_dtmf(
                        call_connection_client=call_connection_client,
                        play_audio=True,
                        operation_context="recognize-employee-id", 
                        play_back_audio_file=".wav",
                        delegation_key=False)
                except Exception as e:
                    logging.error(f"Error in RecognizeFailed event: {e}")


            elif event_type == "Microsoft.Communication.RecognizeCanceled":
                logging.info('RecognizeCanceled event received')
            
            elif event_type == "Microsoft.Communication.SendDtmfCompleted":
                logging.info('SendDtmfCompleted event received')


                # Start audio playback
                try:
                    audio_playback_to_all(call_connection_client, 
                                          callback_url=callback_url, 
                                          operation_context="takk-for-tallet", 
                                          container_name="audio-for-playback", blob_name="takk-for-tallet.wav")
                except Exception as e:
                    logging.error(f"Failed to start audio playback after RecognizeCompleted: {e}")


            elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionToneReceived":
                logging.info('ContinuousDtmfRecognitionToneReceived event received')

                tone = event.get("data").get("tone")
                logging.info(f"Received tone: {tone}")

                if tone:
                    call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))

                    try:
                        # Start audio playback
                        match tone:
                            case "one":
                                stop_continous_dtmf_recognition(call_connection_client, operation_context="stop-continuous-dtmf-recognition")
                                audio_playback_to_all(call_connection_client, 
                                                    operation_context="vennligst-tast-inn-ansattnr", 
                                                    callback_url=callback_url, 
                                                    container_name="audio-for-playback", 
                                                    blob_name="vennligst-tast-inn-ansattnr.wav")
                                # Start DTMF recognition
                                try:
                                    recognize_dtmf(operation_context="recognize-employee-id", 
                                                play_audio=False, 
                                                play_back_audio_file="vennligst-tast-inn-ansattnr.wav",
                                                call_connection_client=call_connection_client)
                                    logging.info("Started DTMF recognition")
                                except Exception as e:
                                    logging.error(f"Failed to start DTMF recognition after CallConnected: {e}")
                            case "two":
                                stop_continous_dtmf_recognition(call_connection_client, operation_context="stop-continuous-dtmf-recognition")
                                audio_playback_to_all(call_connection_client, 
                                            operation_context="denne-samtalen-blir-tatt-opp-deretter-transkribert", 
                                            callback_url=callback_url, 
                                            container_name="audio-for-playback", 
                                            blob_name="denne-samtalen-blir-tatt-opp-deretter-transkribert.wav")
                            case "three":
                                stop_continous_dtmf_recognition(call_connection_client, operation_context="stop-continuous-dtmf-recognition")
                                audio_playback_to_all(call_connection_client, 
                                            operation_context="denne-samtalen-blir-transkribert-i-sanntid", 
                                            callback_url=callback_url, 
                                            container_name="audio-for-playback", 
                                            blob_name="denne-samtalen-blir-transkribert-i-sanntid.wav")
                            # case "four":
                            #     try:
                            #         # audio_playback_to_all(call_connection_client, 
                            #         #                     operation_context="gjentar-tast-en-to-tre-combined", 
                            #         #                     callback_url=callback_url, 
                            #         #                     container_name="audio-for-playback", 
                            #         #                     blob_name="tast-en-to-tre-combined.wav")
                            #         call_connection_client.cancel_all_media_operations()

                            #     except Exception as e:
                            #         logging.error(f"Failed to start audio playback after tast to dtmf: {e}")

                    except Exception as e:
                        logging.error(f"Failed to start audio playback after ContinuousDtmfRecognitionToneReceived: {e}")

                    


            elif event_type == "Microsoft.Communication.PlayCompleted":
                logging.info('PlayCompleted event received')
                operation_context = event.get("data").get("operationContext")
                server_call_id = event.get("data").get("serverCallId")
                logging.info(f"Play operation completed with context: {operation_context}")
                
                match operation_context:
                    case "instruksjoner":
                        call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                        try:
                            audio_playback_to_all(call_connection_client, 
                                                operation_context="tast-en-to-tre-combined", 
                                                callback_url=callback_url,
                                                loop=True,
                                                container_name="audio-for-playback", 
                                                blob_name="tast-en-to-tre-combined.wav")
                        except Exception as e:
                            logging.error(f"Failed to start audio playback after tast to dtmf: {e}")

                    case "denne-samtalen-blir-tatt-opp-deretter-transkribert":
                        recording_properties = CallAutomationSingleton.record_call(input_server_call_id=event.get("data").get("serverCallId"), 
                                                                                pause_on_start=False, callback_url=callback_url)
                        logging.info(f"Recording properties: {recording_properties}")

                        # Start a background thread to stop the recording later
                        recording_id = recording_properties.recording_id
                        threading.Thread(target=stop_recording_after_delay, args=(recording_id, 180)).start()

                    case "denne-samtalen-blir-transkribert-i-sanntid":
                        call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                        
                    case "call-app-tast-en-dtmf":
                        call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                        

                    case "call-app-tast-to-dtmf":
                        call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                        try:
                            audio_playback_to_all(call_connection_client, 
                                                operation_context="denne-samtalen-blir-tatt-opp-deretter-transkribert", 
                                                callback_url=callback_url, 
                                                container_name="audio-for-playback", 
                                                blob_name="denne-samtalen-blir-tatt-opp-deretter-transkribert.wav")
                        except Exception as e:
                            logging.error(f"Failed to start audio playback after tast to dtmf: {e}")

                    case "call-app-tast-tre-dtmf":
                        call_connection_client = CallAutomationSingleton.get_call_connection_client(event.get("data").get("callConnectionId"))
                        try:
                            audio_playback_to_all(call_connection_client, 
                                                operation_context="denne-samtalen-blir-transkribert-i-sanntid", 
                                                callback_url=callback_url, 
                                                container_name="audio-for-playback", 
                                                blob_name="denne-samtalen-blir-transkribert-i-sanntid.wav")
                        except Exception as e:
                            logging.error(f"Failed to start audio playback after tast to dtmf: {e}")


                    
                
            elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionStopped":
                try:
                    logging.info('ContinuousDtmfRecognitionStopped event received')
                except Exception as e:
                    logging.error(f"Error processing ContinuousDtmfRecognitionStopped event: {e}")

            elif event_type == "Microsoft.Communication.RecordingStateChanged":
                try:
                    logging.info('RecordingStateChanged event received')
                    state = event.get("data").get("state")
                    logging.info(f"Recording state changed to: {state}")
                except Exception as e:
                    logging.error(f"Error processing RecordingStateChanged event: {e}")

            elif event_type == "Microsoft.Communication.PlayFailed":
                try:
                    logging.info('PlayFailed event received')
                    result_information = event.get("resultInformation")
                    logging.info(f"Play failed with result: {result_information}")
                except Exception as e:
                    logging.error(f"Error processing PlayFailed event: {e}")

            elif event_type == "Microsoft.Communication.CallDisconnected":
                try:
                    logging.info('CallDisconnected event received')
                    call_id = event.get("data").get("callConnectionId")
                    logging.info(f"Call disconnected with ID: {call_id}")

                    # tasks = running_tasks.get(call_id, [])
                    # if any(not task.done() for task in tasks):
                    #     # There are still unfinished tasks for this call
                    #     logging.info("Cleaning up async tasks for call...")
                    #     cleanup_call(running_tasks, call_id)
                except Exception as e:
                    logging.error(f"Error processing CallDisconnected event: {e}")

        return func.HttpResponse("Events received", status_code=200)

    except Exception as e:
        logging.error(f"Callback Error: {e}")
        return func.HttpResponse("Error processing event", status_code=500)



@app.route(route="generate-user-and-token", auth_level=func.AuthLevel.ANONYMOUS)
@app.function_name(name="GenerateUserAndToken")
def generate_user_and_token(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')
    try:
        communication_identity_client = CommunicationIdentityClient(os.getenv("ACS_ENDPOINT"), AzureKeyCredential(os.getenv("ACS_KEY")))
        logging.info(f"Created communication_identity_client using acs endpoint and key: {communication_identity_client}")
        user, token = communication_identity_client.create_user_and_token(["voip"])
    except ClientAuthenticationError as e:
        logging.error(f"Error creating user and token using acs endpoint and key: {e}")
        logging.info(f"Creating user and token using connection string instead...")
        communication_identity_client = CommunicationIdentityClient.from_connection_string(acs_connection_string)
        logging.info(f"Created communication_identity_client using connection string: {communication_identity_client}")
        user, token = communication_identity_client.create_user_and_token(["voip"])
    except Exception as e:
        logging.error(f"Error creating user and token: {e}")
        return func.HttpResponse(f"Error creating user and token: {e}", status_code=500)

    logging.info(f"Created user: {user}")
    logging.info(f"Created token: {token}")
    try:
        user_and_token_data = {
            "user": {
                "kind": user.kind.value,
                "properties": user.properties,
                "rawId": user.raw_id
            },
            "token": {
                "tokenValue": token.token,
                "expiresOn": token.expires_on
            }
        }

        json_user_and_token_data = json.dumps(user_and_token_data, indent=4)
    except Exception as e:
        logging.error(f"Error converting user and token data to JSON: {e}")
        return func.HttpResponse(f"Error converting user and token data to JSON: {e}", status_code=500)

    logging.info(f"User and token data converted to JSON: {json_user_and_token_data}")

    logging.info("Returning user and token data...")
    return func.HttpResponse(json_user_and_token_data, 
                            status_code=200, 
                            headers={"Access-Control-Allow-Origin": "http://localhost:8080"
                                    }, 
                            mimetype="application/json")



@app.function_name("TranscriptionAciTestSendBackToAcsUserBlobTrigger")
@app.blob_trigger(arg_name="myblob", path="transkriberinger-aci-test/{name}",
                               connection="AZURE_STORAGE_CONNECTION_STRING") 
def transcription_aci_test_send_back_to_acs_user_blob_trigger(myblob: func.InputStream):
    logging.info(f"Python blob trigger function processed blob \n"
                f"Name: {myblob.name} \n"
                f"Blob Size: {myblob.length} bytes \n")
    
    if myblob.name.endswith(".json") :
        logging.info("JSON file detected. Processing...")
        chat_client = ChatClient(os.getenv("ACS_ENDPOINT"), AzureKeyCredential(os.getenv("ACS_KEY")))
        chat_client.get_chat_thread_client()