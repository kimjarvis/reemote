from reemote.models import RemoteModel


class Remote:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # Define the fields that are considered "common" based on RemoteParams
        common_fields = set(RemoteModel.model_fields.keys())

        # Separate kwargs into common_kwargs and extra_kwargs
        self.common_kwargs = {key: value for key, value in kwargs.items() if key in common_fields}
        self.extra_kwargs = {key: value for key, value in kwargs.items() if key not in common_fields}
