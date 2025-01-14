Application Programming Interface
=================================

The core of Arcana's framework is located under the ``arcana.core`` sub-package,
which contains all the domain-independent logic. Domain-specific extensions
for alternative data stores, dimensions and formats should be placed in
``arcana.data.stores``, ``arcana.data.spaces`` and ``arcana.data.formats``
respectively.


.. warning::
    Under construction



Data Model
----------

Core
~~~~

.. autoclass:: arcana.core.data.store.DataStore

.. autoclass:: arcana.core.data.set.Dataset
    :members: add_source, add_sink

.. autoclass:: arcana.core.data.space.DataSpace

.. autoclass:: arcana.core.data.row.DataRow

.. autoclass:: arcana.core.data.column.DataSource

.. autoclass:: arcana.core.data.column.DataSink

.. autoclass:: arcana.core.data.format.DataItem
    :members: get, put

.. autoclass:: arcana.core.data.format.FileGroup

.. autoclass:: arcana.core.data.format.Field

.. autoclass:: arcana.core.data.format.BaseFile

.. autoclass:: arcana.core.data.format.BaseDirectory

.. autoclass:: arcana.core.data.format.WithSideCars


Stores
~~~~~~

.. autoclass:: arcana.data.stores.common.FileSystem

.. autoclass:: arcana.data.stores.bids.Bids

.. autoclass:: arcana.data.stores.medimage.Xnat

.. autoclass:: arcana.data.stores.medimage.XnatViaCS
    :members: generate_xnat_command, generate_dockerfile, create_wrapper_image


Processing
----------

.. autoclass:: arcana.core.pipeline.Pipeline


Enums
~~~~~

.. autoclass:: arcana.core.enum.ColumnSalience
    :members:
    :undoc-members:
    :member-order: bysource

.. autoclass:: arcana.core.enum.ParameterSalience
    :members:
    :undoc-members:
    :member-order: bysource

.. autoclass:: arcana.core.enum.DataQuality
    :members:
    :undoc-members:
    :member-order: bysource
