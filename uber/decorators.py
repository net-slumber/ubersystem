from sideboard.lib import profile
from uber.common import *


def swallow_exceptions(func):
    """
    Don't allow ANY Exceptions to be raised from this.
    Use this ONLY where it's absolutely needed, such as dealing with locking functionality.
    WARNING: DO NOT USE THIS UNLESS YOU KNOW WHAT YOU'RE DOING :)
    """
    @wraps(func)
    def swallow_exception(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:
            log.error("Exception raised, but we're going to ignore it and continue.", exc_info=True)
    return swallow_exception


def log_pageview(func):
    @wraps(func)
    def with_check(*args, **kwargs):
        with sa.Session() as session:
            try:
                attendee = session.admin_account(cherrypy.session['account_id'])
            except:
                pass  # we don't care about unrestricted pages for this version
            else:
                sa.PageViewTracking.track_pageview()
        return func(*args, **kwargs)
    return with_check


def redirect_if_at_con_to_kiosk(func):
    @wraps(func)
    def with_check(*args, **kwargs):
        if c.AT_THE_CON and c.KIOSK_REDIRECT_URL:
            raise HTTPRedirect(c.KIOSK_REDIRECT_URL)
        return func(*args, **kwargs)
    return with_check


def check_if_can_reg(func):
    @wraps(func)
    def with_check(*args, **kwargs):
        is_dealer_get = c.HTTP_METHOD == 'GET' and \
            c.PAGE_PATH == '/preregistration/dealer_registration'
        is_dealer_post = c.HTTP_METHOD == 'POST' and \
            int(kwargs.get('badge_type', 0)) == c.PSEUDO_DEALER_BADGE and \
            int(kwargs.get('tables', 0)) > 0
        is_dealer_reg = c.DEALER_REG_OPEN and (is_dealer_get or is_dealer_post)

        if c.DEV_BOX:
            pass  # Don't redirect to any of the pages below.
        elif c.BADGES_SOLD >= c.MAX_BADGE_SALES:
            return render('static_views/prereg_soldout.html')
        elif c.BEFORE_PREREG_OPEN and not is_dealer_reg:
            return render('static_views/prereg_not_yet_open.html')
        elif c.AFTER_PREREG_TAKEDOWN and not c.AT_THE_CON:
            return render('static_views/prereg_closed.html')
        return func(*args, **kwargs)
    return with_check


def get_innermost(func):
    return get_innermost(func.__wrapped__) if hasattr(func, '__wrapped__') else func


def site_mappable(func):
    func.site_mappable = True
    return func


def suffix_property(func):
    func._is_suffix_property = True
    return func


def _suffix_property_check(inst, name):
    if not name.startswith('_'):
        suffix = '_' + name.rsplit('_', 1)[-1]
        prop_func = getattr(inst, suffix, None)
        if getattr(prop_func, '_is_suffix_property', False):
            field_name = name[:-len(suffix)]
            field_val = getattr(inst, field_name)
            return prop_func(field_name, field_val)

suffix_property.check = _suffix_property_check


def department_id_adapter(func):
    argspec = inspect.getfullargspec(get_innermost(func))
    if 'department_id' not in argspec.args:
        return func
    arg_index = argspec.args.index('department_id')
    possible_args = ('location', 'department', 'department_id')

    @wraps(func)
    def _adapter(*args, **kwargs):
        argvalues = inspect.getargvalues(inspect.currentframe())
        has_kwarg = False
        department_id = None
        for arg in possible_args:
            if arg in kwargs:
                has_kwarg = True
                department_id = kwargs[arg]
                del kwargs[arg]

        if has_kwarg:
            from uber.models.department import Department
            department_id = Department.to_id(department_id)
            return func(*args, department_id=department_id, **kwargs)
        elif arg_index < len(args):
            from uber.models.department import Department
            args = list(args)
            args[arg_index] = Department.to_id(args[arg_index])
            return func(*args, **kwargs)
        return func(*args, **kwargs)

    return _adapter


@department_id_adapter
def check_dept_admin(session, department_id=None):
    from uber.models import AdminAccount, DeptMembership
    account_id = cherrypy.session['account_id']
    admin_account = session.query(AdminAccount).get(account_id)
    if c.ACCOUNTS not in admin_account.access_ints:
        dh_filter = [
            AdminAccount.id == account_id,
            AdminAccount.attendee_id == DeptMembership.attendee_id,
            DeptMembership.is_dept_head == True]

        if department_id:
            dh_filter.append(DeptMembership.department_id == department_id)

        is_dept_head = session.query(AdminAccount).filter(*dh_filter).first()
        if not is_dept_head:
            return 'You must be a department head to complete that action.'


def requires_dept_admin(func):
    @wraps(func)
    def protected(*args, **kwargs):
        if cherrypy.request.method == 'POST':
            department_id = kwargs.get('department_id',
                kwargs.get('department',
                    kwargs.get('location',
                        kwargs.get('id'))))

            from uber.models import Session
            with Session() as session:
                message = check_dept_admin(session, department_id)
                assert not message, message
        return func(*args, **kwargs)
    return protected


def csrf_protected(func):
    @wraps(func)
    def protected(*args, csrf_token, **kwargs):
        check_csrf(csrf_token)
        return func(*args, **kwargs)
    return protected


def ajax(func):
    """decorator for Ajax POST requests which require a CSRF token and return JSON"""
    @wraps(func)
    def returns_json(*args, **kwargs):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        assert cherrypy.request.method == 'POST', 'POST required, got {}'.format(cherrypy.request.method)
        check_csrf(kwargs.pop('csrf_token', None))
        return json.dumps(func(*args, **kwargs), cls=serializer).encode('utf-8')
    returns_json.ajax = True
    return returns_json


def ajax_gettable(func):
    """
    Decorator for page handlers which return JSON.  Unlike the above @ajax decorator,
    this allows either GET or POST and does not check for a CSRF token, so this can
    be used for pages which supply data to external APIs as well as pages used for
    periodically polling the server for new data by our own Javascript code.
    """
    @wraps(func)
    def returns_json(*args, **kwargs):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        return json.dumps(func(*args, **kwargs), cls=serializer).encode('utf-8')
    return returns_json


def multifile_zipfile(func):
    func.site_mappable = True

    @wraps(func)
    def zipfile_out(self, session):
        zipfile_writer = BytesIO()
        with zipfile.ZipFile(zipfile_writer, mode='w') as zip_file:
            func(self, zip_file, session)

        # must do this after creating the zip file as other decorators may have changed this
        # for example, if a .zip file is created from several .csv files, they may each set content-type.
        cherrypy.response.headers['Content-Type'] = 'application/zip'
        cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=' + func.__name__ + '.zip'

        return zipfile_writer.getvalue()
    return zipfile_out


def _set_csv_base_filename(base_filename):
    """
    Set the correct headers when outputting CSV files to specify the filename the browser should use
    """
    cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=' + base_filename + '.csv'


def csv_file(func):
    parameters = inspect.getargspec(func)
    if len(parameters[0]) == 3:
        func.site_mappable = True

    @wraps(func)
    def csvout(self, session, set_headers=True, **kwargs):
        writer = StringIO()
        func(self, csv.writer(writer), session, **kwargs)
        output = writer.getvalue().encode('utf-8')

        # set headers last in case there were errors, so end user still see error page
        if set_headers:
            cherrypy.response.headers['Content-Type'] = 'application/csv'
            _set_csv_base_filename(func.__name__)

        return output
    return csvout


def set_csv_filename(func):
    """
    Use this to override CSV filenames, useful when working with aliases and redirects to make it print the correct name
    """
    @wraps(func)
    def change_filename(self, override_filename=None, *args, **kwargs):
        out = func(self, *args, **kwargs)
        _set_csv_base_filename(override_filename or func.__name__)
        return out
    return change_filename


def check_shutdown(func):
    @wraps(func)
    def with_check(self, *args, **kwargs):
        if c.UBER_SHUT_DOWN or c.AT_THE_CON:
            raise HTTPRedirect('index?message={}', 'The page you requested is only available pre-event.')
        else:
            return func(self, *args, **kwargs)
    return with_check


def credit_card(func):
    @wraps(func)
    def charge(self, session, payment_id=None, stripeToken=None, stripeEmail='ignored', **ignored):
        log.debug('PAYMENT: payment_id={}, stripeToken={}', payment_id or 'NONE', stripeToken or 'NONE')

        if ignored:
            log.debug('PAYMENT: received unexpected stripe parameters: {}', ignored)

        try:
            return func(self, session=session, payment_id=payment_id, stripeToken=stripeToken)
        except HTTPRedirect:
            raise
        except:
            error_text = \
                'Got an error while calling charge' \
                '(self, payment_id={!r}, stripeToken={!r}, ignored={}):\n{}\n' \
                '\n IMPORTANT: This could have resulted in an attendee paying and not being' \
                'marked as paid in the database. Definitely double check this.'\
                .format(payment_id, stripeToken, ignored, traceback.format_exc())

            report_critical_exception(msg=error_text, subject='ERROR: MAGFest Stripe error (Automated Message)')
            return traceback.format_exc()
    return charge


def cached(func):
    func.cached = True
    return func


def cached_page(func):
    innermost = get_innermost(func)
    if hasattr(innermost, 'cached'):
        from sideboard.lib import config as sideboard_config
        func.lock = RLock()

        @wraps(func)
        def with_caching(*args, **kwargs):
            fpath = os.path.join(sideboard_config['root'], 'data', func.__module__ + '.' + func.__name__)
            with func.lock:
                if not os.path.exists(fpath) or datetime.now().timestamp() - os.stat(fpath).st_mtime > 60 * 15:
                    contents = func(*args, **kwargs)
                    with open(fpath, 'wb') as f:
                        # Try to write assuming content is a byte first, then try it as a string
                        try:
                            f.write(contents)
                        except:
                            f.write(bytes(contents, 'UTF-8'))
                with open(fpath, 'rb') as f:
                    return f.read()
        return with_caching
    else:
        return func


def timed(func):
    @wraps(func)
    def with_timing(*args, **kwargs):
        before = datetime.now()
        try:
            return func(*args, **kwargs)
        finally:
            log.debug('{}.{} loaded in {} seconds'.format(func.__module__, func.__name__, (datetime.now() - before).total_seconds()))
    return with_timing


def sessionized(func):
    innermost = get_innermost(func)
    if 'session' not in inspect.getfullargspec(innermost).args:
        return func

    @wraps(func)
    def with_session(*args, **kwargs):
        with sa.Session() as session:
            try:
                retval = func(*args, session=session, **kwargs)
                session.expunge_all()
                return retval
            except HTTPRedirect:
                session.commit()
                raise
    return with_session


def renderable_data(data=None):
    data = data or {}
    data['c'] = c
    data.update({m.__name__: m for m in sa.Session.all_models()})
    return data


# render using the first template that actually exists in template_name_list
def render(template_name_list, data=None):
    data = renderable_data(data)
    env = JinjaEnv.env()
    template = env.get_or_select_template(template_name_list)
    rendered = template.render(data)

    # disabled for performance optimzation.  so sad. IT SHALL RETURN
    # rendered = screw_you_nick(rendered, template)  # lolz.

    return rendered.encode('utf-8')


def render_empty(template_name_list):
    env = JinjaEnv.env()
    template = env.get_or_select_template(template_name_list)
    return str(open(template.filename, 'r').read())


# this is a Magfest inside joke.
# Nick gets mad when people call Magfest a "convention".  He always says "It's not a convention, it's a festival"
# So........ if Nick is logged in.... let's annoy him a bit :)
def screw_you_nick(rendered, template):
    if not c.AT_THE_CON and sa.AdminAccount.is_nick() and 'emails' not in template and 'history' not in template and 'form' not in rendered:
        return rendered.replace('festival', 'convention').replace('Fest', 'Con')  # lolz.
    else:
        return rendered


def get_module_name(class_or_func):
    return class_or_func.__module__.split('.')[-1]


def _get_template_filename(func):
    return os.path.join(get_module_name(func), func.__name__ + '.html')


def prettify_breadcrumb(str):
    return str.replace('_', ' ').title()


def renderable(func):
    @wraps(func)
    def with_rendering(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
        except CSRFException as e:
            message = "Your CSRF token is invalid. Please go back and try again."
            uber.server.log_exception_with_verbose_context(str(e))
            raise HTTPRedirect("../common/invalid?message={}", message)
        except (AssertionError, ValueError) as e:
            message = str(e)
            uber.server.log_exception_with_verbose_context(message)
            raise HTTPRedirect("../common/invalid?message={}", message)
        else:
            try:
                result['breadcrumb_page_pretty_'] = prettify_breadcrumb(func.__name__) if func.__name__ != 'index' else 'Home'
                result['breadcrumb_page_'] = func.__name__ if func.__name__ != 'index' else ''
            except:
                pass

            try:
                result['breadcrumb_section_pretty_'] = prettify_breadcrumb(get_module_name(func))
                result['breadcrumb_section_'] = get_module_name(func)
            except:
                pass

            if c.UBER_SHUT_DOWN and not cherrypy.request.path_info.startswith('/schedule'):
                return render('closed.html')
            elif isinstance(result, dict):
                return render(_get_template_filename(func), result)
            else:
                return result

    return with_rendering


def unrestricted(func):
    func.restricted = False
    return func


def restricted(func):
    @wraps(func)
    def with_restrictions(*args, **kwargs):
        if func.restricted:
            if func.restricted == (c.SIGNUPS,):
                if not cherrypy.session.get('staffer_id'):
                    raise HTTPRedirect('../signups/login?message=You+are+not+logged+in', save_location=True)

            elif cherrypy.session.get('account_id') is None:
                raise HTTPRedirect('../accounts/login?message=You+are+not+logged+in', save_location=True)

            else:
                access = sa.AdminAccount.access_set()
                if not c.AT_THE_CON:
                    access.discard(c.REG_AT_CON)

                if not set(func.restricted).intersection(access):
                    if len(func.restricted) == 1:
                        return 'You need {} access for this page'.format(dict(c.ACCESS_OPTS)[func.restricted[0]])
                    else:
                        return ('You need at least one of the following access levels to view this page: '
                            + ', '.join(dict(c.ACCESS_OPTS)[r] for r in func.restricted))

        return func(*args, **kwargs)
    return with_restrictions


def set_renderable(func, access):
    """
    Return a function that is flagged correctly and is ready to be called by cherrypy as a request
    """
    func.restricted = getattr(func, 'restricted', access)
    new_func = profile(timed(cached_page(sessionized(restricted(renderable(func))))))
    new_func.exposed = True
    return new_func


class all_renderable:
    def __init__(self, *needs_access):
        self.needs_access = needs_access

    def __call__(self, klass):
        for name, func in klass.__dict__.items():
            if hasattr(func, '__call__'):
                new_func = set_renderable(func, self.needs_access)
                setattr(klass, name, new_func)
        return klass


class Validation:
    def __init__(self):
        self.validations = defaultdict(OrderedDict)

    def __getattr__(self, model_name):
        def wrapper(func):
            self.validations[model_name][func.__name__] = func
            return func
        return wrapper

validation, prereg_validation = Validation(), Validation()


adjustment_counter = count().__next__


def presave_adjustment(func):
    """
    Decorate methods on a model class with this decorator to ensure that the
    method is called immediately before the model is saved so that you can
    make any adjustments, e.g. setting a ribbon based on other information.
    """
    func.presave_adjustment = adjustment_counter()
    return func


def predelete_adjustment(func):
    """
    Decorate methods on a model class with this decorator to ensure that the
    method is called immediately before the model is deleted, e.g. to shift
    badges around the now-open slot.
    """
    func.predelete_adjustment = adjustment_counter()
    return func


class cost_property(property):
    """
    Different events have extra things they charge money for to attendees and
    groups.  Those events can use the @Session.model_mixin decorator and then
    define a @cost_property which returns the amount added.  For example, we
    have code in the MAGStock repo which looks vaguely like this:

        @Session.model_mixin
        class Attendee:
            purchased_food = Column(Boolean, default=False)

            @cost_property
            def food_price(self):
                return c.FOOD_PRICE if self.purchased_food else 0
    """


class cached_classproperty(property):
    """
    Like @cached_property except it works on classes instead of instances.
    """
    def __init__(self, fget, *arg, **kw):
        super(cached_classproperty, self).__init__(fget, *arg, **kw)
        self.__doc__ = fget.__doc__
        self.__fget_name__ = fget.__name__

    def __get__(desc, self, cls):
        cache_attr = '_cached_{}_{}'.format(desc.__fget_name__, cls.__name__)
        if not hasattr(cls, cache_attr):
            setattr(cls, cache_attr, desc.fget(cls))
        return getattr(cls, cache_attr)


def create_redirect(url, access=[c.PEOPLE]):
    """
    Return a function which redirects to the given url when called.
    """
    def redirect(self):
        raise HTTPRedirect(url)
    renderable_func = set_renderable(redirect, access)
    return renderable_func


class alias_to_site_section(object):
    """
    Inject a URL redirect from another page to the decorated function.
    This is useful for downstream plugins to add or change functions in upstream plugins to modify their behavior.

    Example: if you move the explode_kittens() function from the core's site_section/summary.py page to a plugin,
    in that plugin you can create an alias back to the original function like this:

    @alias_to_site_section('summary')
    def explode_kittens(...):
        ...

    Please note that this doesn't preserve arguments, it just causes a redirect.  It's most useful for pages without
    arguments like reports and landing pages.
    """
    def __init__(self, site_section_name, alias_name=None, url=None):
        self.site_section_name = site_section_name
        self.alias_name = alias_name
        self.url = url

    def __call__(self, func):
        root = getattr(uber.site_sections, self.site_section_name).Root
        redirect_func = create_redirect(self.url or '../' + get_module_name(func) + '/' + func.__name__)
        setattr(root, self.alias_name or func.__name__, redirect_func)
        return func


def id_required(model):
    def model_id_required(func):
        @wraps(func)
        def check_id(*args, **params):
            check_id_for_model(model=model, **params)
            return func(*args, **params)
        return check_id
    return model_id_required


def check_id_for_model(model, **params):
    message = None

    session = params['session']
    model_id = params.get('id')

    if not model_id:
        message = "No ID provided. Try using a different link or going back."
    elif model_id == 'None':
        # Some pages use the string 'None' is indicate that a new model should be created, so this is a valid ID
        pass
    else:
        try:
            if not isinstance(model_id, uuid.UUID):
                uuid.UUID(model_id)
        except ValueError:
            message = "That ID is not a valid format. Did you enter or edit it manually or paste it incorrectly?"
        else:
            if not session.query(model).filter(model.id == model_id).first():
                message = "The ID provided was not found in our database."

    if message:
        log.error("check_id {} error: {}: id={}", model.__name__, message, model_id)
        raise HTTPRedirect('../preregistration/not_found?id={}&message={}', model_id, message)
