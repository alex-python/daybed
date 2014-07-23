"""Main entry point
"""
import os
import logging
import pkg_resources
from collections import defaultdict


#: Module version, as defined in PEP-0396.
__version__ = pkg_resources.get_distribution(__package__).version

logger = logging.getLogger(__name__)

import six
from cornice import Service
from pyramid.config import Configurator
from pyramid.events import NewRequest
from pyramid.renderers import JSONP
from pyramid.authentication import BasicAuthAuthenticationPolicy
from pyramid.security import (
    unauthenticated_userid
)

from pyramid_multiauth import MultiAuthenticationPolicy

from daybed.acl import (
    RootFactory, DaybedAuthorizationPolicy, check_api_token,
)
from daybed.backends.exceptions import TokenNotFound
from daybed.hkdf import hmac
from daybed.views.errors import unauthorized_view
from daybed.renderers import GeoJSON


def home(request):
    try:
        token = get_token(request)
    except TokenNotFound:
        token = defaultdict(str)
    return {'token': token}


def get_token(request):
    userid = unauthenticated_userid(request)
    hmacId = hmac(userid, request.hawkHmacKey)
    return {
        'hmacId': hmacId,
        'id': userid,
        'key': request.db.get_token(hmacId),
        'algorithm': 'sha256'
    }


def settings_expandvars(settings):
    """Expands all environment variables in a settings dictionary.
    """
    return dict((key, os.path.expandvars(value))
                for key, value in six.iteritems(settings))


def build_list(variable):
    if not variable:
        return []
    elif "\n" in variable:
        variable = variable.split("\n")
    else:
        variable = variable.split(",")
    return [v.strip() for v in variable]


def main(global_config, **settings):
    Service.cors_origins = ('*',)

    settings = settings_expandvars(settings)
    config = Configurator(settings=settings, root_factory=RootFactory)
    config.include("cornice")

    # ACL management

    policies = [
        BasicAuthAuthenticationPolicy(check_api_token),
    ]
    authn_policy = MultiAuthenticationPolicy(policies)

    # Unauthorized view
    config.add_forbidden_view(unauthorized_view)

    # Authorization policy
    authz_policy = DaybedAuthorizationPolicy(
        model_creators=build_list(settings.get("daybed.can_create_model",
                                               "Everyone")),
        token_creators=build_list(settings.get("daybed.can_create_token",
                                               "Everyone")),
        token_managers=build_list(settings.get("daybed.can_manage_token",
                                               None)),
    )
    config.set_authentication_policy(authn_policy)
    config.set_authorization_policy(authz_policy)
    config.add_request_method(get_token, 'token', reify=True)

    # We need to scan AFTER setting the authn / authz policies
    config.scan("daybed.views")

    # backend initialisation
    backend_class = config.maybe_dotted(settings['daybed.backend'])
    config.registry.backend = backend_class.load_from_config(config)

    # hawkHmacKey configuration
    config.registry.hawkHmacKey = settings['daybed.hawkHmacKey'].decode('hex')

    def add_db_to_request(event):
        event.request.db = config.registry.backend
        event.request.hawkHmacKey = config.registry.hawkHmacKey

    config.add_subscriber(add_db_to_request, NewRequest)

    config.add_renderer('jsonp', JSONP(param_name='callback'))

    config.add_renderer('geojson', GeoJSON())
    return config.make_wsgi_app()
