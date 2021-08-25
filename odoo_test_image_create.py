import base64
import logging
import time

from random_words import RandomNicknames

import odoo

_logger = logging.getLogger(__name__)

rn = RandomNicknames()


def execute(self, partner_nb=1):

    odoo.cli.server.setup_pid_file()

    with open('/home/seb/repo/useful-things/oscar-sutton-yihlaRCCvd4-unsplash.jpg', 'rb') as f:
        big_image = base64.b64encode(f.read())

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    _logger.info('Starting to create partners')

    start = time.time()

    partners = self.env['res.partner'].create([{
        'name': name,
        'image_1920': big_image,
    } for name in rn.random_nicks(count=partner_nb)])

    _logger.info('Done creating %d partners in %.3fs' % (len(partners), time.time() - start))
    # self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
