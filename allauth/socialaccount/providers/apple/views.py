import json
import requests
from datetime import timedelta

from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.csrf import csrf_exempt

import jwt

from allauth.socialaccount.models import SocialApp, SocialToken
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import (
    OAuth2Adapter,
    OAuth2CallbackView,
    OAuth2LoginView,
)

from .client import AppleOAuth2Client
from .provider import AppleProvider


class AppleOAuth2Adapter(OAuth2Adapter):
    provider_id = AppleProvider.id
    access_token_url = "https://appleid.apple.com/auth/token"
    authorize_url = "https://appleid.apple.com/auth/authorize"
    public_key_url = "https://appleid.apple.com/auth/keys"

    def get_public_key(self, id_token):
        """
        Get the public key which matches the `kid` in the id_token header.
        """
        kid = jwt.get_unverified_header(id_token)["kid"]
        apple_public_key = [
            d
            for d in requests.get(self.public_key_url).json()["keys"]
            if d["kid"] == kid
        ][0]
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(
            json.dumps(apple_public_key)
        )
        return public_key

    def get_client_id(self, provider):
        app = SocialApp.objects.get(provider=provider.id)
        return [aud.strip() for aud in app.client_id.split(",")]

    def parse_token(self, data):
        try:
            token = SocialToken(token=data["access_token"])
            token.token_secret = data.get("refresh_token", "")

            public_key = self.get_public_key(data["id_token"])
            provider = self.get_provider()
            allowed_auds = self.get_client_id(provider)

            token.user_data = jwt.decode(
                data["id_token"],
                public_key,
                algorithms=["RS256"],
                verify=True,
                audience=allowed_auds,
            )
            expires_in = data.get(self.expires_in_key, None)
            if expires_in:
                token.expires_at = timezone.now() + timedelta(
                    seconds=int(expires_in)
                )
            return token
        except jwt.PyJWTError as e:
            raise OAuth2Error("Invalid id_token") from e

    def complete_login(self, request, app, token, **kwargs):
        extra_data = token.user_data
        login = self.get_provider().sociallogin_from_response(
            request, extra_data
        )
        login.state["id_token"] = token.user_data
        return login


class AppleOAuth2ClientMixin:
    def get_client(self, request, app):
        client = super().get_client(request, app)
        apple_client = AppleOAuth2Client(
            client.request,
            client.consumer_key,
            client.consumer_secret,
            client.access_token_method,
            client.access_token_url,
            client.callback_url,
            client.scope,
            key=client.key,
            cert=client.cert,
        )
        return apple_client


class AppleOAuth2LoginView(AppleOAuth2ClientMixin, OAuth2LoginView):
    """
    Custom AppleOAuth2LoginView to return AppleOAuth2Client
    """

    pass


class AppleOAuth2CallbackView(AppleOAuth2ClientMixin, OAuth2CallbackView):
    """
    Custom OAuth2CallbackView because `Sign In With Apple`:
        * returns AppleOAuth2Client
        * Apple requests callback by POST
    """

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            url = request.build_absolute_uri(request.get_full_path())
            params = {
                "code": request.POST.get("code"),
                "state": request.POST.get("state"),
            }
            return HttpResponseRedirect("%s?%s" % (url, urlencode(params)))
        if request.method == "GET":
            return super().dispatch(request, *args, **kwargs)


oauth2_login = AppleOAuth2LoginView.adapter_view(AppleOAuth2Adapter)
oauth2_callback = csrf_exempt(
    AppleOAuth2CallbackView.adapter_view(AppleOAuth2Adapter)
)
