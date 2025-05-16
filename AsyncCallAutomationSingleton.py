import azure.communication.callautomation.aio as aio_az_call
from azure.communication.callautomation.aio import CallAutomationClient as AioCallAutomationClient
from azure.communication.identity import CommunicationUserIdentifier, CommunicationIdentifierKind, PhoneNumberIdentifier
from azure.core.exceptions import HttpResponseError
import asyncio
import logging

from utility import return_file_source_with_sas_token
from config import callback_url

class AsyncCallAutomationSingleton:
    _instance = None
    _acs_connection_string = None
    _instances = []

    @classmethod
    def configure(cls, acs_connection_string: str):
        cls._acs_connection_string = acs_connection_string

    @classmethod
    async def get_instance(cls) -> aio_az_call.CallAutomationClient:
        async with asyncio.Lock():
            if cls._instance is None:
                if cls._acs_connection_string:
                    cls._instance = AioCallAutomationClient.from_connection_string(cls._acs_connection_string)
                else:
                    raise ValueError("No connection string provided")
            return cls._instance
        
    @classmethod
    def get_new_client(cls) -> aio_az_call.CallAutomationClient:
        if not cls._acs_connection_string:
            raise ValueError("ACS connection string not configured")
        aio_call_automation_client = AioCallAutomationClient.from_connection_string(cls._acs_connection_string)
        cls._instances.append(aio_call_automation_client)
        return aio_call_automation_client

    @classmethod
    async def get_call_connection_client(cls, call_connection_id) -> aio_az_call.CallConnectionClient:
        client = await cls.get_instance()
        return client.get_call_connection(call_connection_id)

    @classmethod
    async def audio_playback_to_all(
        cls,
        client: aio_az_call.CallAutomationClient,
        call_connection_id: str,
        operation_context: str, 
        loop: bool = False,
        interrupt_call_media_operation: bool = True,
        callback_url: str = callback_url, 
        container_name: str = "audio-for-playback", 
        blob_name: str = "instruksjoner.wav", 
        generate_sas: bool = True
        ):
        try:
            call_connection_client = client.get_call_connection(call_connection_id)
            logging.info("Retrieving audio file source")
            play_source = return_file_source_with_sas_token(container_name=container_name, blob_name=blob_name, generate_sas=generate_sas)
            logging.info('Started running audio_playback_to_all-function')
            await call_connection_client.play_media_to_all(
                play_source=play_source,
                operation_callback_url=callback_url,
                operation_context=operation_context,
                loop=loop,
                interrupt_call_media_operation=interrupt_call_media_operation
            )
        except Exception as e:
            logging.error(f"Unexpected error in audio_playback_to_all-function: {e}")
        

    @classmethod
    async def start_continous_dtmf_recognition(cls, client: aio_az_call.CallAutomationClient, call_connection_id: str, operation_context: str):
        call_connection_client = client.get_call_connection(call_connection_id)
        logging.info('Started running start_continous_dtmf_recognition-function')
        try:
            call_props = await call_connection_client.get_call_properties()
            user_raw_id = call_props.source.raw_id
            user_kind = call_props.source.kind

            match user_kind:
                case CommunicationIdentifierKind.PHONE_NUMBER:
                    await call_connection_client.start_continuous_dtmf_recognition(
                        target_participant=PhoneNumberIdentifier(call_props.source.properties.get("value")),
                        operation_context=operation_context,
                    )
                case CommunicationIdentifierKind.COMMUNICATION_USER:
                    await call_connection_client.start_continuous_dtmf_recognition(
                        target_participant=CommunicationUserIdentifier(user_raw_id),
                        operation_context=operation_context,
                    )

            logging.info("Started continuous DTMF recognition")
        except HttpResponseError as e:
            logging.error(f"HTTP error during starting continuous DTMF recognition: {e.message}")
        except Exception as e:
            logging.error(f"Unexpected error in start_continous_dtmf_recognition-function: {e}")



    @classmethod
    async def stop_continous_dtmf_recognition(
        cls,
        operation_context: str):
        call_connection_client = cls.get_instance().call_connection_client
        logging.info('Started running stop_continous_dtmf_recognition-function')
        try:
            user_raw_id = await call_connection_client.get_call_properties().source.raw_id
            user_kind = call_connection_client.get_call_properties().source.kind
            match user_kind:
                case aio_az_call.CommunicationIdentifierKind.PHONE_NUMBER:
                    await call_connection_client.stop_continuous_dtmf_recognition(
                        target_participant=PhoneNumberIdentifier(call_connection_client.get_call_properties().source.properties.get("value")),
                        operation_context=operation_context,
                    )
                case aio_az_call.CommunicationIdentifierKind.COMMUNICATION_USER:
                    await call_connection_client.stop_continuous_dtmf_recognition(
                        target_participant=CommunicationUserIdentifier(user_raw_id),
                        operation_context=operation_context,
                    )
            logging.info("Stopped continuous DTMF recognition")
        except HttpResponseError as e:
            logging.error(f"HTTP error during stopping continuous DTMF recognition: {e.message}")
        except Exception as e:
            logging.error(f"Unexpected error in stop_continous_dtmf_recognition-function: {e}")
