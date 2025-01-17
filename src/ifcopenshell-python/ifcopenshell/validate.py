from __future__ import print_function

import sys
import json
import functools

from collections import namedtuple

import ifcopenshell

named_type = ifcopenshell.ifcopenshell_wrapper.named_type
aggregation_type = ifcopenshell.ifcopenshell_wrapper.aggregation_type
simple_type = ifcopenshell.ifcopenshell_wrapper.simple_type
type_declaration = ifcopenshell.ifcopenshell_wrapper.type_declaration
enumeration_type = ifcopenshell.ifcopenshell_wrapper.enumeration_type
entity_type = ifcopenshell.ifcopenshell_wrapper.entity
select_type = ifcopenshell.ifcopenshell_wrapper.select_type
attribute = ifcopenshell.ifcopenshell_wrapper.attribute


class ValidationError(Exception):
    pass


log_entry_type = namedtuple("log_entry_type", ("level", "message", "instance"))


class json_logger:
    def __init__(self):
        self.statements = []
        self.instance = None

    def set_instance(self, instance):
        self.instance = instance

    def log(self, level, message, *args, **kwargs):
        self.statements.append(log_entry_type(level, message % args, kwargs.get("instance"))._asdict())

    def __getattr__(self, level):
        return functools.partial(self.log, level, instance=self.instance)


simple_type_python_mapping = {
    # @todo should include unicode for Python2
    "string": str,
    "integer": int,
    "real": float,
    "number": float,
    "boolean": bool,
    "logical": {True, False, "UNKNOWN"},
    "binary": str,  # maps to a str of "0" and "1"
}


def assert_valid_inverse(attr, val, schema):
    b1, b2 = attr.bound1(), attr.bound2()
    invalid = len(val) < b1 or (b2 != -1 and len(val) > b2)
    if invalid:
        raise ValidationError("%r not valid for %s" % (val, attr))
    return True


def assert_valid(attr, val, schema):
    if isinstance(attr, attribute):
        attr_type = attr.type_of_attribute()
    else:
        attr_type = attr

    type_wrappers = (named_type,)
    if not isinstance(val, ifcopenshell.entity_instance):
        # If val is not an entity instance we need to
        # flatten the type declaration to something that
        # maps to the python types
        type_wrappers += (type_declaration,)

    while isinstance(attr_type, type_wrappers):
        attr_type = attr_type.declared_type()

    invalid = False

    if isinstance(attr_type, simple_type):
        simple_type_python = simple_type_python_mapping[attr_type.declared_type()]
        if type(simple_type_python) == set:
            invalid = val not in simple_type_python
        else:
            invalid = type(val) != simple_type_python
    elif isinstance(attr_type, (entity_type, type_declaration)):
        invalid = not isinstance(val, ifcopenshell.entity_instance) or not val.is_a(attr_type.name())
    elif isinstance(attr_type, select_type):
        val_to_use = val
        if isinstance(schema.declaration_by_name(val.is_a()), enumeration_type):
            if isinstance(val, ifcopenshell.entity_instance):
                val_to_use = val.wrappedValue
            else:
                invalid = True
        if not invalid:
            invalid = not any(try_valid(x, val_to_use, schema) for x in attr_type.select_list())
    elif isinstance(attr_type, enumeration_type):
        invalid = val not in attr_type.enumeration_items()
    elif isinstance(attr_type, aggregation_type):
        b1, b2 = attr_type.bound1(), attr_type.bound2()
        ty = attr_type.type_of_element()
        invalid = len(val) < b1 or (b2 != -1 and len(val) > b2) or not all(assert_valid(ty, v, schema) for v in val)
    else:
        raise NotImplementedError("Not impl %s %s" % (type(attr_type), attr_type))

    if invalid:
        raise ValidationError("%r not valid for %s" % (val, attr))

    return True


def try_valid(attr, val, schema):
    try:
        return assert_valid(attr, val, schema)
    except ValidationError as e:
        return False


def validate(f, logger):
    """
    For an IFC population model `f` validate whether the entity attribute values are correctly supplied. As this
    is a function that is applied after a file has been parsed, certain types of errors in syntax, duplicate
    numeric identifiers or invalidate entity names are not caught by this function. Some of these might have been
    logged and can be retrieved by calling `ifcopenshell.get_log()`. A verification of the type, entity and global
    WHERE rules is also not implemented.

    For every entity instance in the model, it is checked that the entity is not abstract that every attribute value
    is of the correct type and that the inverse attributes are of the correct cardinality.

    Express simple types are checked for their valuation type. For select types it is asserted that the value conforms
    to one of the leaves. For enumerations it is checked that the value is indeed on of the items. For aggregations it
    is checked that the elements and the cardinality conforms. Type declarations (IfcInteger which is an integer) are
    unpacked until one of the above cases is reached.
    """
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema)
    for inst in f:
        if hasattr(logger, "set_instance"):
            logger.set_instance(inst)

        entity = schema.declaration_by_name(inst.is_a())
        attrs = entity.all_attributes()

        if entity.is_abstract():
            e = "Entity %s is abstract" % entity.name()
            if hasattr(logger, "set_instance"):
                logger.error(e)
            else:
                logger.error("In %s\n%s", inst, e)

        has_invalid_value = False
        for i in range(len(attrs)):
            try:
                inst[i]
                pass
            except:
                if hasattr(logger, "set_instance"):
                    logger.error("Invalid attribute value for %s.%s", entity, attrs[i])
                else:
                    logger.error(
                        "In %s\nInvalid attribute value for %s.%s",
                        inst,
                        entity,
                        attrs[i],
                    )
                has_invalid_value = True

        if not has_invalid_value:
            for attr, val, is_derived in zip(attrs, inst, entity.derived()):

                if val is None and not (is_derived or attr.optional()):
                    logger.error("Attribute %s.%s not optional", entity, attr)

                if val is not None:
                    attr_type = attr.type_of_attribute()
                    try:
                        assert_valid(attr, val, schema)
                    except ValidationError as e:
                        if hasattr(logger, "set_instance"):
                            logger.error(str(e))
                        else:
                            logger.error("In %s\n%s", inst, e)

        for attr in entity.all_inverse_attributes():
            val = getattr(inst, attr.name())
            try:
                assert_valid_inverse(attr, val, schema)
            except ValidationError as e:
                if hasattr(logger, "set_instance"):
                    logger.error(str(e))
                else:
                    logger.error("In %s\n%s", inst, e)


if __name__ == "__main__":
    import sys
    import logging

    filenames = [x for x in sys.argv[1:] if not x.startswith("--")]
    flags = set(x for x in sys.argv[1:] if x.startswith("--"))

    for fn in filenames:
        if "--json" in flags:
            logger = json_logger()
        else:
            logger = logging.getLogger("validate")
            logger.setLevel(logging.DEBUG)

        f = ifcopenshell.open(fn)

        print("Validating", fn, file=sys.stderr)
        validate(f, logger)

        if "--json" in flags:
            print("\n".join(json.dumps(x, default=str) for x in logger.statements))
