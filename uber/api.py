from uber.common import *

__version__ = 'v0.1'

attendee_fields = [
    'full_name', 'first_name', 'last_name', 'email', 'zip_code', 'cellphone',
    'ec_name', 'ec_phone', 'badge_status_label', 'checked_in',
    'badge_type_label', 'ribbon_labels', 'staffing', 'is_dept_head',
    'assigned_depts_labels', 'weighted_hours', 'worked_hours', 'badge_num']

fields = dict({
    'food_restrictions': {
        'sandwich_pref_labels': True,
        'standard_labels': True,
        'freeform': True
    },
    'shifts': {
        'worked_label': True,
        'job': [
            'type_label', 'department_name', 'name', 'description',
            'start_time', 'end_time', 'extra15'
        ]
    }
}, **{field: True for field in attendee_fields})


class AttendeeLookup:
    def lookup(self, badge_num):
        with Session() as session:
            attendee = session.query(Attendee).filter_by(badge_num=badge_num).first()
            return attendee.to_dict(fields) if attendee else {'error': 'No attendee found with Badge #{}'.format(badge_num)}

    def search(self, query):
        with Session() as session:
            return [a.to_dict(fields) for a in session.search(query).all()]

job_fields = dict({
    'name': True,
    'description': True,
    'department_name': True,
    'start_time': True,
    'end_time': True,
    'duration': True,
    'shifts': {
        'attendee': {
            'badge_num': True,
            'full_name': True,
            'first_name': True,
            'last_name': True,
            'email': True,
            'cellphone': True,
            'badge_printed_name': True
        }
    }
})


class JobLookup:
    def lookup(self, location):
        with Session() as session:
            query = session.query(Job).filter_by(department_name=location) \
                .options(subqueryload(Job.department))
            return [job.to_dict(job_fields) for job in query]


class DepartmentLookup:
    def list(self):
        return dict(c.DEPARTMENT_OPTS)

config_fields = [
    'EVENT_NAME',
    'ORGANIZATION_NAME',
    'YEAR',
    'EPOCH',

    'EVENT_VENUE',
    'EVENT_VENUE_ADDRESS',

    'AT_THE_CON',
    'POST_CON',

]


class ConfigLookup:
    def info(self):
        output = {
            'API_VERSION': __version__
        }
        for field in config_fields:
            output[field] = getattr(c, field)
        return output

    def lookup(self, field):
        if field.upper() in config_fields:
            return getattr(c, field.upper())

if c.API_ENABLED:
    services.register(AttendeeLookup(), 'attendee')
    services.register(JobLookup(), 'shifts')
    services.register(DepartmentLookup(), 'dept')
    services.register(ConfigLookup(), 'config')
