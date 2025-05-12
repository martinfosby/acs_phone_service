from azure.communication.identity import CommunicationUserIdentifier
from azure.communication.callautomation import CallAutomationClient
from CallAutomationSingleton import CallAutomationSingleton
from typing import Dict
import os

class MockCommunicationIdentityClient:
    def create_user(self) -> CommunicationUserIdentifier:
        # Simulate a fake user ID
        return CommunicationUserIdentifier("fake-user-id")

    def get_token(self, user: CommunicationUserIdentifier, scopes: list) -> Dict:
        # Return a mock token
        return {"token": "fake-token", "expires_on": 9999999999}
    


client = CallAutomationSingleton.get_instance(os.getenv("ACS_CONNECTION_STRING"))

# Call an ACS user
target_user = {"identifier": {"raw_id": "8:acs:4aa62437-d905-441f-a3f7-3f8d4cf85da6_00000027-65a5-4bdd-a7ac-473a0d007413", "kind": "communicationUser"}}
client.create_call(target_user, callback_url=os.getenv("CALLBACK_URL"))