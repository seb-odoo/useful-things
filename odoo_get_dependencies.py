
def main(self, names):
    modules = self.env['ir.module.module'].search([('name', 'in', names)])
    dep = modules + modules.downstream_dependencies(exclude_states=('fake'))
    print(','.join(['*/%s/*' % m.name for m in dep]))


if __name__ == "__main__":
    names = ['website_sale']
    main(self, names)
