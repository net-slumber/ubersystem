from __future__ import division
from common import *

def search_fields(att):
    return [att.full_name, att.last_first, att.badge, att.comments, att.admin_notes, att.email] \
           + ([att.group.name] if att.group else [])

def check_everything(attendee):
    if state.AT_THE_CON and attendee.id is None:
        if isinstance(attendee.badge_num, str) or attendee.badge_num < 0:
            return "Invalid badge number"
        elif attendee.id is None and Attendee.objects.filter(badge_type=attendee.badge_type, badge_num=attendee.badge_num).count():
            return "Another attendee already exists with that badge number"
    
    if attendee.is_dealer and not attendee.group:
        return "Dealers must be associated with a group"
    
    message = check(attendee)
    if message:
        return message
    
    if state.AT_THE_CON and attendee.age_group == AGE_UNKNOWN and attendee.id is None:
        return "You must enter this attendee's age group"

def unassigned_counts():
    return {row["group_id"]: row["unassigned"]
            for row in Attendee.objects.exclude(group=None)
                                       .filter(first_name="")
                                       .values("group_id")
                                       .annotate(unassigned=Count("id"))}

def search(text):
    q = Q()
    for attr in ["first_name","last_name","badge_num","email","comments","admin_notes","group__name"]:
        q |= Q(**{attr + "__icontains": text})
    return Attendee.objects.filter(q)

@all_renderable(PEOPLE)
class Root:
    def index(self, message="", show="some", page="1", search_text="", uploaded_id="", order="last_name"):
        order_by = [order, "first_name"] if order.endswith("last_name") else [order]
        total_count = Attendee.objects.count()
        count = 0
        if search_text:
            attendees = search(search_text)
            count = attendees.count()
        if not count:
            attendees = Attendee.objects.all()
            count = total_count
        attendees = attendees.select_related("group").order_by(*order_by)
        
        if search_text and count == total_count:
            message = "No matches found"
        elif search_text and not state.AT_THE_CON and count == 1:
            raise HTTPRedirect("form?id={}&message={}", attendees[0].id, "This attendee was the only search result")
        
        page = int(page)
        pages = range(1, int(math.ceil(count / 100)) + 1)
        if show == "some":
            attendees = attendees[-100 + 100*page : 100*page]
        
        return {
            "message":        message if isinstance(message, basestring) else message[-1],
            "show":           show,
            "page":           page,
            "pages":          pages,
            "search_text":    search_text,
            "search_results": bool(search_text),
            "attendees":      attendees,
            "order":          Order(order),
            "attendee_count": total_count,
            "checkin_count":  Attendee.objects.exclude(checked_in__isnull = True).count(),
            "attendee":       Attendee.objects.get(id = uploaded_id) if uploaded_id else None
        }
    
    def form(self, message="", return_to="", **params):
        attendee = get_model(Attendee, params, checkgroups = ["interests"],
                             bools = ["staffing","trusted","international","placeholder","got_merch","can_spam"])
        if "first_name" in params:
            attendee.group = None if not params["group_opt"] else Group.objects.get(id = params["group_opt"])
            
            message = check_everything(attendee)
            if not message:
                attendee.save()
                
                if return_to:
                    raise HTTPRedirect(return_to + "&message={}", "Attendee data uploaded")
                else:
                    raise HTTPRedirect("index?uploaded_id={}&message={}", attendee.id, "has been uploaded")
        
        return {
            "message":    message,
            "attendee":   attendee,
            "return_to":  return_to,
            "group_opts": [(b.id, b.name) for b in Group.objects.order_by("name")],
            "unassigned": unassigned_counts()
        }
    
    def change_badge(self, message="", **params):
        attendee = get_model(Attendee, dict(params, badge_num = params.get("newnum") or 0))
        
        if "badge_type" in params:
            preassigned = state.AT_THE_CON or attendee.badge_type in PREASSIGNED_BADGE_TYPES
            if preassigned:
                message = check(attendee)
            
            if not message:
                message = change_badge(attendee)
                raise HTTPRedirect("form?id={}&message={}", attendee.id, message)
        
        return {
            "message":  message,
            "attendee": attendee
        }
    
    def history(self, id):
        attendee = Attendee.objects.get(id = id)
        Tracking.objects.filter(links__contains = "Attendee({})".format(id))
        return {
            "attendee": attendee,
            "emails":   Email.objects.filter(Q(dest = attendee.email) 
                                           | Q(fk_tab = "Attendee", fk_id = id))
                                     .order_by("when"),
            "changes":  Tracking.objects.filter(Q(model = "Attendee", fk_id = id)
                                              | Q(links__contains = "Attendee({})".format(id)))
                                        .order_by("when")
        }
    
    def delete(self, id):
        attendee = Attendee.objects.get(id=id)
        attendee.delete()
        if attendee.group:
            Attendee.objects.create(group = attendee.group, paid = attendee.paid,
                                    badge_type = attendee.badge_type, badge_num = attendee.badge_num)
            raise HTTPRedirect("index?message={}", "Attendee deleted, but badge " + attendee.badge + " is still available to be assigned to someone else")
        raise HTTPRedirect("index?message={}", "Attendee deleted")
    
    @ajax
    def record_mpoint_usage(self, badge_num, amount):
        try:
            attendee = Attendee.objects.get(badge_num = badge_num)
        except:
            return {"success":False, "message":"No one has badge number {}".format(badge_num)}
        
        mpu = MPointUse(attendee = attendee, amount = amount)
        message = check(mpu)
        if message:
            return {"success":False, "message":message}
        else:
            mpu.save()
            message = "{mpu.attendee.full_name} exchanged {mpu.amount} MPoints for cash".format(mpu = mpu)
            return {"id":mpu.id, "success":True, "message":message}
    
    def undo_mpoint_usage(self, id):
        MPointUse.objects.get(id=id).delete()
        return "MPoint usage deleted"
    
    @ajax
    def record_mpoint_exchange(self, badge_num, mpoints):
        try:
            attendee = Attendee.objects.get(badge_num = badge_num)
        except:
            return {"success":False, "message":"No one has badge number {}".format(badge_num)}
        
        mpe = MPointExchange(attendee = attendee, mpoints = mpoints)
        message = check(mpe)
        if message:
            return {"success":False, "message":message}
        else:
            mpe.save()
            message = "{mpe.attendee.full_name} exchanged {mpe.mpoints} of last year's MPoints".format(mpe = mpe)
            return {"id":mpe.id, "success":True, "message":message}
    
    def undo_mpoint_exchange(self, id):
        MPointExchange.objects.get(id=id).delete()
        return "MPoint exchange deleted"
    
    @ajax
    def record_sale(self, **params):
        sale = get_model(Sale, params)
        message = check(sale)
        if message:
            return {"success":False, "message":message}
        else:
            sale.save()
            message = "{sale.what} sold for ${sale.cash}".format(sale = sale)
            return {"id":sale.id, "success":True, "message":message}
    
    def undo_sale(self, id):
        Sale.objects.get(id=id).delete()
        return "Sale deleted"
    
    @ajax
    def check_in(self, id, badge_num, age_group):
        attendee = Attendee.objects.get(id=id)
        pre_paid = attendee.paid
        pre_amount = attendee.amount_paid
        pre_badge = attendee.badge_num
        success, increment = True, False
        
        if not attendee.badge_num:
            message = check_range(badge_num, attendee.badge_type)
            if not message:
                maybe_dupe = Attendee.objects.filter(badge_num=badge_num, badge_type=attendee.badge_type)
                if maybe_dupe:
                    message = "That badge number already belongs to " + maybe_dupe[0].full_name
            success = not message
        
        if success and attendee.checked_in:
            message = attendee.full_name + " was already checked in!"
        elif success:
            message = ""
            attendee.checked_in = datetime.now()
            attendee.age_group = int(age_group)
            if not attendee.badge_num:
                attendee.badge_num = int(badge_num)
            if attendee.paid == NOT_PAID:
                attendee.paid = HAS_PAID
                attendee.amount_paid = attendee.total_cost
                message += "<b>This attendee has not paid for their badge; make them pay ${0}!</b> <br/>".format(attendee.total_cost)
            attendee.save()
            increment = True
            
            message += "{0.full_name} checked in as {0.badge} with {0.accoutrements}".format(attendee)
            if attendee.staffing:
                if attendee.weighted_hours:
                    message += "<br/> Please give this staffer their schedule"
                else:
                    message += "<br/> This staffer is not signed up for hours; tell them to talk to a department head ASAP"
        
        return {
            "success":    success,
            "message":    message,
            "increment":  increment,
            "badge":      attendee.badge,
            "paid":       attendee.get_paid_display(),
            "age_group":  attendee.get_age_group_display(),
            "pre_paid":   pre_paid,
            "pre_amount": pre_amount,
            "pre_badge":  pre_badge,
            "checked_in": attendee.checked_in and hour_day_format(attendee.checked_in)
        }
    
    def undo_checkin(self, id, pre_paid, pre_amount, pre_badge):
        a = Attendee.objects.get(id = id)
        a.checked_in, a.paid, a.amount_paid, a.badge_num = None, pre_paid, pre_amount, pre_badge
        a.save()
        return "Attendee successfully un-checked-in"
    
    def recent(self):
        return {"attendees": Attendee.objects.order_by("-registered")}
    
    def merch(self, message=""):
        return {"message": message}
    
    @ajax
    def check_merch(self, badge_num):
        id = tshirt = None
        if not (badge_num.isdigit() and 0 < int(badge_num) < 99999):
            message = "Invalid badge number"
        else:
            results = Attendee.objects.filter(badge_num = badge_num)
            if results.count() != 1:
                message = "No attendee has badge number {}".format(badge_num)
            else:
                attendee = results[0]
                if not attendee.merch:
                    message = "{a.full_name} ({a.badge}) has no merch".format(a = attendee)
                elif attendee.got_merch:
                    message = "{a.full_name} ({a.badge}) already got {a.merch}".format(a = attendee)
                else:
                    id, tshirt = attendee.id, attendee.tshirt
                    message = "{a.full_name} ({a.badge}) has not yet received {a.merch}".format(a = attendee)
        return {
            "id": id,
            "tshirt": tshirt,
            "message": message
        }
    
    @ajax
    def give_merch(self, id, shirt_size):
        success = False
        attendee = Attendee.objects.get(id = id)
        if not attendee.merch:
            message = "{} has no merch".format(attendee.full_name)
        elif attendee.got_merch:
            message = "{} already got {}".format(attendee.full_name, attendee.merch)
        else:
            message = "{} is now marked as having received {}".format(attendee.full_name, attendee.merch)
            attendee.got_merch = True
            attendee.save()
            if shirt_size and shirt_size.isdigit():
                Tracking.objects.create(
                    fk_id = id,
                    model = "Attendee",
                    which = repr(attendee),
                    who = Account.admin_name(),
                    action = CREATED,
                    data = "shirt => " + dict(SHIRT_OPTS)[int(shirt_size)]
                )
            success = True
        
        return {
            "id": id,
            "success": success,
            "message": message
        }
    
    def take_back_merch(self, id):
        attendee = Attendee.objects.get(id = id)
        attendee.got_merch = False
        attendee.save()
        return "{a.full_name} ({a.badge}) merch handout canceled".format(a = attendee)
    
    if state.AT_THE_CON or DEV_BOX:
        @unrestricted
        def register(self, message="", **params):
            params["id"] = "None"
            attendee = get_model(Attendee, params, bools=["international"], checkgroups=["interests"], restricted=True)
            if "first_name" in params:
                if not attendee.first_name or not attendee.last_name:
                    message = "First and Last Name are required fields"
                elif attendee.ec_phone[:1] != "+" and len(re.compile("[0-9]").findall(attendee.ec_phone)) != 10:
                    message = "Enter a 10-digit emergency contact number"
                elif not attendee.age_group:
                    message = "Please select an age category"
                elif attendee.badge_type not in [ATTENDEE_BADGE, ONE_DAY_BADGE]:
                    message = "No hacking allowed!"
                else:
                    attendee.badge_num = 0
                    if not attendee.zip_code:
                        attendee.zip_code = "00000"
                    attendee.save()
                    message = "Thank you for registering!  Please queue in the line and have your photo ID and signed waiver ready.  If you have not already paid, please also have ${} ready.".format(attendee.total_cost)
                    raise HTTPRedirect("register?message={}", message)
            
            return {
                "message":  message,
                "attendee": attendee
            }
    
    def comments(self, order = "last_name"):
        return {
            "order": Order(order),
            "attendees": Attendee.objects.exclude(comments = "").order_by(order)
        }
    
    def new(self, message="", checked_in=""):
        groups = set()
        for a in Attendee.objects.filter(first_name="", group__isnull=False).select_related("group"):
            groups.add((a.group.id, a.group.name or "BLANK"))
        
        return {
            "message":    message,
            "checked_in": checked_in,
            "groups":     sorted(groups, key = lambda tup: tup[1]),
            "recent":     Attendee.objects.filter(badge_num=0, registered__gte = datetime.now() - timedelta(minutes=90))
                                          .exclude(first_name = "")
                                          .order_by("registered")
        }
    
    def new_checkin(self, id, badge_num, ec_phone="", message="", group=""):
        checked_in = ""
        attendee = Attendee.objects.get(id=id)
        existing = list(Attendee.objects.filter(badge_num = badge_num))
        if existing:
            message = "{0.badge} already belongs to {0.full_name}".format(existing[0])
        else:
            badge_type, message = get_badge_type(badge_num)
            attendee.badge_type = badge_type
            attendee.badge_num = badge_num
            if group:
                group = Group.objects.get(id = group)
                with BADGE_LOCK:
                    available = [a for a in group.attendee_set.filter(first_name = "")]
                    matching = [a for a in available if a.badge_type == badge_type]
                    if not available:
                        message = "The last badge for that group has already been assigned by another station"
                    elif not matching:
                        message = "Badge #{} is a {} badge, but {} has no badges of that type".format(badge_num, attendee.get_badge_type_display(), group.name)
                    else:
                        for attr in ["group","paid","amount_paid","ribbon"]:
                            setattr(attendee, attr, getattr(matching[0], attr))
                        matching[0].delete()
            else:
                attendee.paid = HAS_PAID
                attendee.amount_paid = attendee.total_cost
        
        if not message:
            attendee.ec_phone = ec_phone
            attendee.checked_in = datetime.now()
            attendee.save()
            message = "{a.full_name} checked in as {a.badge} with {a.accoutrements}".format(a = attendee)
            checked_in = attendee.id
        
        raise HTTPRedirect("new?message={}&checked_in={}", message, checked_in)
    
    def mark_as_paid(self, id):
        attendee = Attendee.objects.get(id = id)
        attendee.paid = HAS_PAID
        attendee.amount_paid = attendee.total_cost
        attendee.save()
        raise HTTPRedirect("new?message={}", "Attendee marked as paid")
    
    def undo_new_checkin(self, id):
        attendee = Attendee.objects.get(id = id)
        if attendee.group:
            unassigned = Attendee.objects.create(group = attendee.group, paid = PAID_BY_GROUP, badge_type = attendee.badge_type, ribbon = attendee.ribbon)
            unassigned.registered = datetime(state.EPOCH.year, 1, 1)
            unassigned.save()
        attendee.badge_num = 0
        attendee.checked_in = attendee.group = None
        attendee.save()
        raise HTTPRedirect("new?message={}", "Attendee un-checked-in but still marked as paid")
    
    def shifts(self, id, shift_id="", message=""):
        attendee = Attendee.objects.get(id = id)
        return {
            "message":  message,
            "shift_id": shift_id,
            "attendee": attendee,
            "shifts":   Shift.serialize(attendee.shift_set.all())
        }
    
    def update_nonshift(self, id, nonshift_hours):
        attendee = Attendee.objects.get(id = id)
        if not re.match("^[0-9]+$", nonshift_hours):
            raise HTTPRedirect("shifts?id={}&message={}", attendee.id, "Invalid integer")
        
        attendee.nonshift_hours = nonshift_hours
        attendee.save()
        raise HTTPRedirect("shifts?id={}&message={}", attendee.id, "Non-shift hours updated")
    
    def update_notes(self, id, admin_notes):
        attendee = Attendee.objects.get(id = id)
        attendee.admin_notes = admin_notes
        attendee.save()
        raise HTTPRedirect("shifts?id={}&message={}", id, "Admin notes updated")
    
    def assign(self, staffer_id, job_id):
        message = assign(staffer_id, job_id) or "Shift added"
        raise HTTPRedirect("shifts?id={}&message={}", staffer_id, message)
    
    def unassign(self, shift_id):
        shift = Shift.objects.get(id=shift_id)
        shift.delete()
        raise HTTPRedirect("shifts?id={}&message={}", shift.attendee.id, "Staffer unassigned from shift")
    
    def feed(self, page = "1", who = "", what = ""):
        feed = Tracking.objects.exclude(action = AUTO_BADGE_SHIFT).order_by("-id")
        if who:
            feed = feed.filter(who = who)
        if what:
            feed = feed.filter(Q(data__icontains = what) | Q(which__icontains = what))
        return {
            "who": who,
            "what": what,
            "page": page,
            "count": feed.count(),
            "feed": get_page(page, feed),
            "who_opts": Tracking.objects.values_list("who", flat=True).order_by("who").distinct(),
        }
    
    def staffers(self, message="", order="first_name"):
        shifts = defaultdict(list)
        for shift in Shift.objects.select_related():
            shifts[shift.attendee].append(shift)
        
        staffers = list(Attendee.objects.filter(staffing = True))
        for staffer in staffers:
            staffer._shifts = shifts[staffer]
        
        return {
            "order": Order(order),
            "message": message,
            "staffer_count": len(staffers),
            "total_hours": sum(j.weighted_hours * j.slots for j in Job.objects.all()),
            "taken_hours": sum(s.job.weighted_hours for s in Shift.objects.select_related()),
            "staffers": sorted(staffers, key = lambda a: getattr(a, order.lstrip("-")), reverse = order.startswith("-"))
        }
    
    def hotel_eligible(self):
        by_dept = defaultdict(list)
        for attendee in Attendee.objects.filter(badge_type = STAFF_BADGE).order_by("first_name","last_name"):
            for dept in attendee.assigned_display:
                by_dept[dept].append(attendee)
        return {"by_dept": sorted(by_dept.items())}
    
    def hotel_ordered(self):
        reqs = [hr for hr in HotelRequests.objects.select_related() if hr.nights]
        
        names = {}
        for hr in reqs:
            names.setdefault(hr.attendee.last_name.lower(), set()).add(hr.attendee)
        
        lookup = defaultdict(set)
        for xs in names.values():
            for attendee in xs:
                lookup[attendee] = xs
        
        for req in reqs:
            for word in req.wanted_roommates.lower().replace(",", "").split():
                try:
                    combined = lookup[list(names[word])[0]] | lookup[req.attendee]
                    for attendee in combined:
                        lookup[attendee] = combined
                except:
                    pass
        
        grouped = {frozenset(group) for group in lookup.values()}
        cherrypy.response.headers["Content-Type"] = "text/csv"
        cherrypy.response.headers["Content-Disposition"] = "attachment; filename=hotel.csv"
        out = StringIO()
        writer = csv.writer(out)
        writer.writerow(["Name","Email","Phone","Nights","Departments","Roomate Requests","Roomate Anti-Requests","Special Needs"])
        for group in grouped:
            for i in range(3):
                writer.writerow([])
            for a in group:
                hr = a.hotel_requests
                writer.writerow([a.full_name, a.email, a.phone, " / ".join(a.hotel_nights), " / ".join(a.assigned_display),
                                 hr.wanted_roommates, hr.unwanted_roommates, hr.special_needs])
        return out.getvalue()
    
    def hotel_requests(self):
        requests = HotelRequests.objects.order_by("attendee__first_name", "attendee__last_name")
        return {
            "staffer_count": Attendee.objects.filter(badge_type = STAFF_BADGE).count(),
            "declined_count": requests.filter(nights = "").count(),
            "requests": requests.exclude(nights = "")
        }
    
    def hotel_hours(self):
        staffers = list(Attendee.objects.filter(badge_type = STAFF_BADGE).order_by("first_name","last_name"))
        staffers = [s for s in staffers if s.hotel_shifts_required and s.weighted_hours < 30]
        return {"staffers": staffers}
    
    @ajax
    def approve(self, id, approved):
        hr = HotelRequests.objects.get(id = id)
        if approved == "approved":
            hr.approved = True
        else:
            hr.nights = ",".join(night for night in hr.nights.split(",") if int(night) in {THURSDAY,FRIDAY,SATURDAY})
        hr.save()
        return {"nights": " / ".join(hr.attendee.hotel_nights)}
    
    def review(self):
        return {"attendees": Attendee.objects.exclude(for_review = "").order_by("first_name","last_name")}
