from azure.communication.identity import CommunicationUserIdentifier
from typing import Dict

class MockCommunicationIdentityClient:
    def create_user(self) -> CommunicationUserIdentifier:
        # Simulate a fake user ID
        return CommunicationUserIdentifier("fake-user-id")

    def get_token(self, user: CommunicationUserIdentifier, scopes: list) -> Dict:
        # Return a mock token
        return {"token": "fake-token", "expires_on": 9999999999}