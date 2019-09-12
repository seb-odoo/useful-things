import logging
import time

from random_words import RandomNicknames

import odoo

_logger = logging.getLogger(__name__)

rn = RandomNicknames()


def execute(self):

    odoo.cli.server.setup_pid_file()

    website = self.env['website'].browse(1)

    views = [
        'website.404',
        'website.layout',
        'website.show_website_info',
        'website.assets_backend',
        'website.brand_promotion',
        'portal.frontend_layout',
        'portal.portal_show_sign_in',
        'portal.record_pager',
        'portal.portal_sidebar',
        'web.webclient_bootstrap',
    ]

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    _logger.info('Starting to viewref')

    start = time.time()

    for view in views:
        website.viewref(view)

    _logger.info('Done viewref in %.3fs' % (time.time() - start))
    # self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
