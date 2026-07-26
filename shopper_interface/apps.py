from django.apps import AppConfig


class ShopperInterfaceConfig(AppConfig):
    name = 'shopper_interface'

    def ready(self):
        import shopper_interface.signals  # noqa