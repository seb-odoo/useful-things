
import logging
import time

from random_words import RandomNicknames

import odoo

_logger = logging.getLogger(__name__)

rn = RandomNicknames()


def execute(self, partner_nb=1000):

    odoo.cli.server.setup_pid_file()

    vals_list = [{
        'name': name,
    } for name in rn.random_nicks(count=partner_nb)]

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    _logger.info('Starting to create %d partners' % len(vals_list))

    start = time.time()

    partners = self.env['res.partner'].create(vals_list)

    partners.flush()

    _logger.info('Done creating %d partners in %.3fs' % (len(partners), time.time() - start))
    # self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
